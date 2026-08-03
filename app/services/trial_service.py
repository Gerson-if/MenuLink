"""
Ciclo de vida do período de teste grátis (trial) de cada loja.

Chamado periodicamente pelo comando `flask process-trials` (ver
app/cli.py) — não há um agendador embutido na aplicação (sem
APScheduler/Celery), então quem instala o Comanda precisa programar
esse comando num cron/systemd timer do servidor (ver README, seção de
deploy) rodando pelo menos uma vez por dia.

Várias coisas acontecem aqui, por loja em TRIAL com `trial_ends_at`
definido — a "régua de comunicação ativa":

1. Lembrete automático CONFIGURÁVEL (dias de antecedência, ciclo de
   repetição e texto, todos ajustáveis pelo Super Admin) — enviado pelo
   chat de suporte enquanto ainda faltam poucos dias para o fim do
   teste.
2. Aviso FIXO de "faltam 2 dias" — texto fixo, não editável, mandado
   uma vez só, como reforço de urgência além do lembrete configurável
   acima.
3. Aviso único de "seu teste acabou, mas você tem N dias de tolerância"
   — mandado uma vez só, assim que o prazo normal vence.
4. Aviso FIXO no DIA em que o link efetivamente sai do ar (último dia
   da tolerância) — texto fixo, mandado uma vez só, antes da suspensão
   de fato acontecer.
5. Suspensão automática (status -> BLOCKED_PAYMENT, reaproveitando a
   mesma tela de "conta bloqueada por pendência" que já existe) quando
   a tolerância também se esgota sem a loja ter sido regularizada.

Uma loja deixa de ser tocada por este serviço assim que o Super Admin
mudar o status dela manualmente (ativar, suspender, bloquear, cancelar)
— o filtro abaixo só busca quem ainda está em TenantStatus.TRIAL.
"""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Tenant, TenantStatus
from app.models.platform_settings import PlatformSettings
from app.services.chat_service import ChatService

# Aviso fixo de "faltam 2 dias" — número fixo por decisão de produto
# (diferente do lembrete configurável, que usa
# PlatformSettings.trial_reminder_days_before/cycle_days).
D2_WARNING_DAYS = 2


class TrialService:
    def __init__(self):
        self.settings = PlatformSettings.get_or_create()

    def process_all(self, now: datetime | None = None) -> dict:
        """Roda a checagem para todas as lojas em teste. Retorna um
        resumo (contadores) usado pela saída do comando de CLI."""
        now = now or datetime.now(timezone.utc)

        tenants = Tenant.query.filter(
            Tenant.status == TenantStatus.TRIAL,
            Tenant.trial_ends_at.isnot(None),
        ).all()

        summary = {
            "reminders_sent": 0, "d2_notices_sent": 0, "grace_notices_sent": 0,
            "final_notices_sent": 0, "suspended": 0,
        }

        for tenant in tenants:
            # SQLite não preserva o timezone na leitura (mesmo com
            # DateTime(timezone=True)) — sempre gravamos em UTC (ver
            # AdminTenantService.create), então é seguro assumir UTC aqui
            # se vier "naive" (mesmo padrão de Tenant.trial_status).
            trial_ends_at = tenant.trial_ends_at
            if trial_ends_at.tzinfo is None:
                trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)

            if now > trial_ends_at and tenant.plan_confirmed_at is not None:
                # Lojista já tinha confirmado um plano pago durante o
                # teste (ver TenantSettingsService.change_plan) — em vez
                # de entrar na régua de tolerância/suspensão abaixo,
                # ativa a assinatura de verdade agora, com o ciclo
                # começando exatamente em trial_ends_at (não hoje: ele
                # não perde os dias grátis restantes).
                self._activate_confirmed_plan(tenant, trial_ends_at.date())
                summary["activated"] = summary.get("activated", 0) + 1
                continue

            grace_ends_at = trial_ends_at + timedelta(days=self.settings.trial_grace_days)

            if now > grace_ends_at:
                self._suspend(tenant, now)
                summary["suspended"] += 1
                continue

            if now > trial_ends_at:
                if not tenant.trial_grace_notice_sent:
                    self._send_grace_notice(tenant, grace_ends_at)
                    tenant.trial_grace_notice_sent = True
                    summary["grace_notices_sent"] += 1

                # Aviso fixo do "dia da suspensão" — dispara quando HOJE é
                # o próprio dia em que a tolerância se esgota (a
                # suspensão de verdade só acontece na próxima execução do
                # job, depois que o prazo passar de fato).
                if grace_ends_at.date() == now.date() and not tenant.trial_final_notice_sent:
                    self._send_final_day_notice(tenant)
                    tenant.trial_final_notice_sent = True
                    summary["final_notices_sent"] += 1
                continue

            # Aviso fixo de "faltam 2 dias" — reforço extra de urgência,
            # independente do lembrete configurável abaixo.
            days_left = max(0, (trial_ends_at.date() - now.date()).days)
            if days_left <= D2_WARNING_DAYS and not tenant.trial_d2_notice_sent:
                self._send_d2_notice(tenant)
                tenant.trial_d2_notice_sent = True
                summary["d2_notices_sent"] += 1

            reminder_from = trial_ends_at - timedelta(days=self.settings.trial_reminder_days_before)
            if now < reminder_from:
                continue

            last_sent = tenant.trial_reminder_last_sent_at
            if last_sent is not None and last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            due = last_sent is None or (now - last_sent) >= timedelta(days=self.settings.trial_reminder_cycle_days)
            if due:
                self._send_reminder(tenant, now)
                tenant.trial_reminder_last_sent_at = now
                summary["reminders_sent"] += 1

        db.session.commit()
        return summary

    def _activate_confirmed_plan(self, tenant: Tenant, trial_ends_at_date) -> None:
        from app.services.admin_billing_service import AdminBillingService

        AdminBillingService().activate_subscription_after_trial(tenant, trial_ends_at=trial_ends_at_date)
        body = (
            f"Seu período de teste terminou e o plano '{tenant.plan.name}' que você escolheu já está "
            "ativo! A partir de agora, as faturas de renovação aparecem na sua página de assinatura. "
            "Obrigado por continuar com a gente! 🙂"
        ) if tenant.plan else (
            "Seu período de teste terminou e sua assinatura já está ativa! Obrigado por continuar com a gente! 🙂"
        )
        ChatService.send_system_message(tenant.id, body)

    def _send_reminder(self, tenant: Tenant, now: datetime) -> None:
        days_left = max(0, (tenant.trial_ends_at.date() - now.date()).days)
        body = self.settings.trial_reminder_message_or_default.format(
            loja=tenant.name,
            dias_restantes=days_left,
            data_fim=tenant.trial_ends_at.strftime("%d/%m/%Y"),
        )
        ChatService.send_system_message(tenant.id, body)

    def _send_d2_notice(self, tenant: Tenant) -> None:
        """Texto fixo — não passa pelo template configurável em
        PlatformSettings.trial_reminder_message (por decisão de
        produto: essa mensagem final de 2 dias é sempre a mesma)."""
        body = "Oi! Seu cardápio está ficando incrível, mas seu teste acaba em 2 dias. Vamos ativar seu plano?"
        ChatService.send_system_message(tenant.id, body)

    def _send_final_day_notice(self, tenant: Tenant) -> None:
        """Texto fixo, mandado no próprio dia em que o link sai do ar
        (fim da tolerância) — ver comentário em `process_all`."""
        body = "Seu link sairá do ar hoje à meia-noite. Gere sua cobrança aqui para continuar vendendo sem interrupções."
        ChatService.send_system_message(tenant.id, body)

    def _send_grace_notice(self, tenant: Tenant, grace_ends_at: datetime) -> None:
        grace_days = self.settings.trial_grace_days
        body = (
            f"Olá! Seu período de teste grátis do Menu Link chegou ao fim, mas fique "
            f"tranquilo(a): você ainda tem acesso total ao painel por mais "
            f"{grace_days} dia(s), até {grace_ends_at.strftime('%d/%m/%Y')}, para "
            f"regularizar sua situação e continuar usando o sistema sem "
            f"interrupções. Fale com a gente por aqui quando quiser dar esse "
            f"próximo passo! 🙂"
        )
        ChatService.send_system_message(tenant.id, body)

    def _suspend(self, tenant: Tenant, now: datetime) -> None:
        tenant.status = TenantStatus.BLOCKED_PAYMENT
        tenant.blocked_reason = "Período de teste expirado (prazo de regularização encerrado)."
        tenant.blocked_at = now
        body = (
            "Seu período de teste grátis e o prazo extra para regularização "
            "chegaram ao fim, então o acesso ao painel foi pausado por aqui. "
            "Assim que sua situação for regularizada, o acesso volta ao normal "
            "automaticamente. Qualquer dúvida, é só responder por aqui — estamos "
            "à disposição!"
        )
        ChatService.send_system_message(tenant.id, body)
