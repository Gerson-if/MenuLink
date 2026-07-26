from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import ChatMessage, Tenant, TenantStatus
from app.models.platform_settings import PlatformSettings
from app.services.admin_tenant_service import AdminTenantService
from app.services.trial_service import TrialService


def _create_trial_tenant(app):
    service = AdminTenantService()
    return service.create(
        name="Loja Trial", email="trial@example.com", phone="", whatsapp_number="",
        plan_id=None, owner_name="Dono Trial", owner_email="dono@trial.example.com",
        owner_password="senha123",
    )


class TestTrialCreation:
    def test_new_tenant_gets_trial_ends_at_from_settings(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.trial_days = 15
            db.session.commit()

            tenant = _create_trial_tenant(app)

            assert tenant.trial_ends_at is not None
            trial_ends_at = tenant.trial_ends_at
            if trial_ends_at.tzinfo is None:
                trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)
            expected = datetime.now(timezone.utc) + timedelta(days=15)
            assert abs((trial_ends_at - expected).total_seconds()) < 60

    def test_trial_status_reports_days_left(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            status = tenant.trial_status()
            assert status["phase"] == "trial"
            assert status["days_left"] > 0

    def test_trial_status_is_none_for_active_tenant(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.trial_status() is None


class TestTrialServiceProcessing:
    def test_sends_reminder_within_window_and_respects_cycle(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            # 4 dias — dentro da janela do lembrete configurável (padrão 5
            # dias de antecedência), mas fora do aviso fixo de "faltam 2
            # dias" (D2_WARNING_DAYS), pra este teste focar só no lembrete
            # configurável/cíclico.
            tenant.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=4)
            db.session.commit()

            summary = TrialService().process_all()
            assert summary["reminders_sent"] == 1
            assert ChatMessage.query.filter_by(tenant_id=tenant.id).count() == 1

            # Rodar de novo imediatamente não deve mandar outro lembrete
            # (ainda dentro do ciclo configurado).
            summary_again = TrialService().process_all()
            assert summary_again["reminders_sent"] == 0
            assert ChatMessage.query.filter_by(tenant_id=tenant.id).count() == 1

    def test_sends_fixed_d2_notice_once(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            tenant.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=2)
            db.session.commit()

            summary = TrialService().process_all()
            assert summary["d2_notices_sent"] == 1
            assert tenant.trial_d2_notice_sent is True
            bodies = [m.body for m in ChatMessage.query.filter_by(tenant_id=tenant.id).all()]
            assert any("acaba em 2 dias" in b for b in bodies)

            summary_again = TrialService().process_all()
            assert summary_again["d2_notices_sent"] == 0

    def test_sends_fixed_final_day_notice_on_last_day_of_grace(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            settings = PlatformSettings.get_or_create()
            # Um "now" de referência fixo (meio-dia), pra evitar flakiness
            # de relógio: trial_ends_at é calculado a partir do MESMO
            # `reference_now` passado a process_all, garantindo que o fim
            # da tolerância caia exatamente nesse dia (nem passou, nem é
            # amanhã ainda).
            reference_now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
            tenant.trial_ends_at = reference_now - timedelta(days=settings.trial_grace_days)
            db.session.commit()

            summary = TrialService().process_all(now=reference_now)
            assert summary["final_notices_sent"] == 1
            assert tenant.trial_final_notice_sent is True
            assert tenant.status == TenantStatus.TRIAL  # ainda não suspensa hoje
            bodies = [m.body for m in ChatMessage.query.filter_by(tenant_id=tenant.id).all()]
            assert any("meia-noite" in b for b in bodies)

            summary_again = TrialService().process_all(now=reference_now)
            assert summary_again["final_notices_sent"] == 0

    def test_sends_grace_notice_once_when_trial_ends(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.session.commit()

            summary = TrialService().process_all()
            assert summary["grace_notices_sent"] == 1
            assert tenant.status == TenantStatus.TRIAL
            assert tenant.trial_grace_notice_sent is True

            summary_again = TrialService().process_all()
            assert summary_again["grace_notices_sent"] == 0

    def test_suspends_tenant_after_grace_period_expires(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            settings = PlatformSettings.get_or_create()
            tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=settings.trial_grace_days + 1)
            db.session.commit()

            summary = TrialService().process_all()
            assert summary["suspended"] == 1

            db.session.refresh(tenant)
            assert tenant.status == TenantStatus.BLOCKED_PAYMENT
            assert "teste" in tenant.blocked_reason.lower()

    def test_ignores_tenants_no_longer_in_trial(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=30)
            tenant.status = TenantStatus.ACTIVE
            db.session.commit()

            summary = TrialService().process_all()
            assert summary == {
                "reminders_sent": 0, "d2_notices_sent": 0, "grace_notices_sent": 0,
                "final_notices_sent": 0, "suspended": 0,
            }


class TestTrialLockoutAccess:
    def test_owner_locked_out_by_expired_trial_can_still_reach_chat(self, app, client, login_as):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            settings = PlatformSettings.get_or_create()
            tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=settings.trial_grace_days + 1)
            db.session.commit()
            TrialService().process_all()

        login_as(client, "dono@trial.example.com", "senha123")

        resp = client.get("/painel/dashboard", follow_redirects=True)
        assert resp.status_code == 200
        assert "conta-bloqueada" in resp.request.path

        chat_resp = client.get("/painel/suporte")
        assert chat_resp.status_code == 200


class TestAdminTrialDeadlineAdjustment:
    def test_super_admin_can_extend_trial_deadline(self, app, client, login_as):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            tenant_id = tenant.id

        login_as(client, "admin@cardapio.saas", "admin123")

        new_date = (datetime.now(timezone.utc) + timedelta(days=45)).date()
        resp = client.post(
            f"/admin/lojistas/{tenant_id}/prazo-teste",
            data={"trial_ends_at": new_date.isoformat()},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "atualizado" in resp.get_data(as_text=True)

        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            assert tenant.trial_ends_at.date() == new_date
            assert tenant.trial_grace_notice_sent is False
            assert tenant.trial_d2_notice_sent is False
            assert tenant.trial_final_notice_sent is False


class TestTrialUrgency:
    def test_is_urgent_false_when_many_days_left(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            status = tenant.trial_status()
            assert status["is_urgent"] is False

    def test_is_urgent_true_within_urgent_days(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            settings = PlatformSettings.get_or_create()
            tenant.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=settings.trial_urgent_days)
            db.session.commit()
            status = tenant.trial_status()
            assert status["is_urgent"] is True

    def test_is_urgent_true_during_grace(self, app):
        with app.app_context():
            tenant = _create_trial_tenant(app)
            tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.session.commit()
            status = tenant.trial_status()
            assert status["phase"] == "grace"
            assert status["is_urgent"] is True
