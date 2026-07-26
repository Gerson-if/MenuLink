"""
Regras de negócio de troca de plano: upgrade imediato com cobrança
prorateada, downgrade seguro (valida limites e só entra em vigor no
fim do ciclo, sem estorno em dinheiro), confirmação de plano ainda
durante o trial (sem perder os dias grátis) e dunning simples de
faturas vencidas — ver TenantSettingsService.change_plan,
AdminBillingService e TrialService.
"""

from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import (
    BillingCycle, Category, Invoice, InvoiceStatus, Plan, Product, Tenant, TenantStatus,
)
from app.models.platform_settings import PlatformSettings
from app.services.admin_billing_service import AdminBillingService
from app.services.tenant_settings_service import TenantSettingsError, TenantSettingsService
from app.services.trial_service import TrialService


def _tenant():
    return Tenant.query.filter_by(slug="braseiro-cia").first()


def _make_plan(name, slug, price_cents, max_products=None, max_categories=None):
    plan = Plan(
        name=name, slug=slug, price_cents=price_cents, billing_cycle=BillingCycle.MONTHLY,
        max_products=max_products, max_categories=max_categories, is_active=True,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


class TestUpgradeImediatoComProrateio:
    def test_upgrade_applies_immediately_and_creates_prorated_invoice(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            subscription = billing.get_or_create_subscription(tenant)
            old_price = subscription.plan.price_cents
            # Força um ciclo previsível (30 dias, metade já passada) pra
            # o prorateio dar um valor exato de calcular na mão.
            subscription.current_period_start = date.today() - timedelta(days=15)
            subscription.current_period_end = date.today() + timedelta(days=15)
            db.session.commit()

            new_plan = _make_plan("Pro", "pro-upgrade", old_price + 5000)
            db.session.commit()

            TenantSettingsService(tenant).change_plan(new_plan.id)

            assert tenant.plan_id == new_plan.id
            assert subscription.plan_id == new_plan.id

            invoices = Invoice.query.filter_by(subscription_id=subscription.id).all()
            assert len(invoices) == 1
            invoice = invoices[0]
            assert invoice.status == InvoiceStatus.PENDING
            # Metade dos dias restantes (~15/30) da diferença de R$50 -> ~R$25.
            assert invoice.amount_cents == pytest.approx(2500, abs=100)
            assert invoice.due_date == subscription.current_period_end

    def test_upgrade_cancels_a_previously_scheduled_downgrade(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            subscription = billing.get_or_create_subscription(tenant)
            cheap_plan = _make_plan("Starter", "starter-cancel-downgrade", 1000)
            subscription.pending_plan_id = cheap_plan.id
            subscription.pending_plan_change_at = subscription.current_period_end
            db.session.commit()

            expensive_plan = _make_plan("Enterprise", "enterprise-cancel-downgrade", 20000)
            TenantSettingsService(tenant).change_plan(expensive_plan.id)

            assert subscription.pending_plan_id is None
            assert subscription.pending_plan_change_at is None
            assert subscription.plan_id == expensive_plan.id


class TestDowngradeSeguro:
    def test_downgrade_blocked_when_product_count_exceeds_new_plan_limit(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            billing.get_or_create_subscription(tenant)

            category = Category(tenant_id=tenant.id, name="Bebidas", slug="bebidas-limite", icon="drink", is_active=True)
            db.session.add(category)
            db.session.flush()
            for i in range(3):
                db.session.add(Product(
                    tenant_id=tenant.id, category_id=category.id, name=f"Produto {i}", slug=f"produto-limite-{i}",
                    price_cents=1000, is_active=True,
                ))
            db.session.commit()

            small_plan = _make_plan("Nano", "nano-bloqueio", 1000, max_products=2)
            db.session.commit()

            with pytest.raises(TenantSettingsError, match="produto"):
                TenantSettingsService(tenant).change_plan(small_plan.id)

    def test_downgrade_blocked_when_category_count_exceeds_new_plan_limit(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            billing.get_or_create_subscription(tenant)

            for i in range(3):
                db.session.add(Category(
                    tenant_id=tenant.id, name=f"Categoria {i}", slug=f"categoria-limite-{i}", icon="other", is_active=True,
                ))
            db.session.commit()

            small_plan = _make_plan("Nano2", "nano-bloqueio-categoria", 1000, max_categories=2)
            db.session.commit()

            with pytest.raises(TenantSettingsError, match="categoria"):
                TenantSettingsService(tenant).change_plan(small_plan.id)

    def test_downgrade_schedules_change_without_refund_and_keeps_current_plan_until_cycle_end(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            subscription = billing.get_or_create_subscription(tenant)
            current_plan_id = subscription.plan_id

            cheap_plan = _make_plan("Starter", "starter-downgrade-agendado", 500, max_products=None, max_categories=None)
            db.session.commit()

            TenantSettingsService(tenant).change_plan(cheap_plan.id)

            # Nada muda AGORA — nem tenant.plan_id, nem subscription.plan_id,
            # nem nenhuma fatura/estorno é gerado.
            assert tenant.plan_id == current_plan_id
            assert subscription.plan_id == current_plan_id
            assert subscription.pending_plan_id == cheap_plan.id
            assert subscription.pending_plan_change_at == subscription.current_period_end
            assert Invoice.query.filter_by(subscription_id=subscription.id).count() == 0

    def test_pending_downgrade_is_applied_at_next_invoice_generation(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            subscription = billing.get_or_create_subscription(tenant)

            cheap_plan = _make_plan("Starter", "starter-aplica-renovacao", 500)
            db.session.commit()
            TenantSettingsService(tenant).change_plan(cheap_plan.id)

            # Simula que o ciclo já virou (a troca agendada só é aplicada
            # quando `pending_plan_change_at` já chegou — ver
            # AdminBillingService._apply_pending_plan_change).
            subscription.pending_plan_change_at = date.today() - timedelta(days=1)
            db.session.commit()

            invoice = billing.generate_next_invoice(subscription)

            assert subscription.plan_id == cheap_plan.id
            assert tenant.plan_id == cheap_plan.id
            assert subscription.pending_plan_id is None
            assert subscription.pending_plan_change_at is None
            assert invoice.amount_cents == cheap_plan.price_cents


class TestConfirmacaoDePlanoDuranteTrial:
    def test_change_plan_during_trial_only_records_intent_no_subscription_created(self, app):
        with app.app_context():
            from app.services.admin_tenant_service import AdminTenantService

            trial_tenant = AdminTenantService().create(
                name="Loja Trial Plano", email="trialplano@example.com", phone="", whatsapp_number="",
                plan_id=None, owner_name="Dono Trial Plano", owner_email="dono@trialplano.example.com",
                owner_password="senha123",
            )
            plan = _make_plan("Pro Trial", "pro-trial-confirma", 9990)
            db.session.commit()

            TenantSettingsService(trial_tenant).change_plan(plan.id)

            assert trial_tenant.plan_id == plan.id
            assert trial_tenant.plan_confirmed_at is not None
            assert trial_tenant.status == TenantStatus.TRIAL

            from app.repositories.billing_repository import SubscriptionRepository
            assert SubscriptionRepository().get_active_for_tenant(trial_tenant.id) is None

    def test_trial_service_activates_subscription_starting_at_trial_end_when_plan_confirmed(self, app):
        with app.app_context():
            from app.services.admin_tenant_service import AdminTenantService

            trial_tenant = AdminTenantService().create(
                name="Loja Trial Ativa", email="trialativa@example.com", phone="", whatsapp_number="",
                plan_id=None, owner_name="Dono Trial Ativa", owner_email="dono@trialativa.example.com",
                owner_password="senha123",
            )
            plan = _make_plan("Pro Trial Ativa", "pro-trial-ativa", 4990)
            db.session.commit()
            TenantSettingsService(trial_tenant).change_plan(plan.id)

            past_trial_end = date.today() - timedelta(days=2)
            from datetime import datetime, timezone
            trial_tenant.trial_ends_at = datetime.combine(past_trial_end, datetime.min.time(), tzinfo=timezone.utc)
            db.session.commit()

            summary = TrialService().process_all()

            assert summary.get("activated") == 1
            db.session.refresh(trial_tenant)
            assert trial_tenant.status == TenantStatus.ACTIVE

            from app.repositories.billing_repository import SubscriptionRepository
            subscription = SubscriptionRepository().get_active_for_tenant(trial_tenant.id)
            assert subscription is not None
            assert subscription.plan_id == plan.id
            assert subscription.current_period_start == past_trial_end

    def test_trial_tenant_without_confirmed_plan_still_follows_normal_grace_flow(self, app):
        with app.app_context():
            from app.services.admin_tenant_service import AdminTenantService

            trial_tenant = AdminTenantService().create(
                name="Loja Trial Sem Confirmar", email="trialsemconfirmar@example.com", phone="",
                whatsapp_number="", plan_id=None, owner_name="Dono", owner_email="dono@trialsc.example.com",
                owner_password="senha123",
            )
            trial_tenant.trial_ends_at = None
            db.session.commit()
            from datetime import datetime, timezone
            trial_tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.session.commit()

            summary = TrialService().process_all()

            assert summary.get("activated", 0) == 0
            assert summary["grace_notices_sent"] == 1
            assert trial_tenant.status == TenantStatus.TRIAL


class TestDunning:
    def test_pending_invoice_past_due_date_is_marked_overdue(self, app):
        with app.app_context():
            tenant = _tenant()
            billing = AdminBillingService()
            invoice = billing.create_invoice(tenant, amount_reais=50.0, due_date=date.today() - timedelta(days=1))

            summary = billing.process_dunning()

            assert summary["marked_overdue"] == 1
            db.session.refresh(invoice)
            assert invoice.status == InvoiceStatus.OVERDUE

    def test_tenant_blocked_after_grace_period_expires(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.dunning_grace_days = 3
            db.session.commit()

            tenant = _tenant()
            tenant.status = TenantStatus.ACTIVE
            billing = AdminBillingService()
            invoice = billing.create_invoice(tenant, amount_reais=50.0, due_date=date.today() - timedelta(days=5))
            billing.mark_overdue(invoice)

            summary = billing.process_dunning()

            assert summary["blocked"] == 1
            db.session.refresh(tenant)
            assert tenant.status == TenantStatus.BLOCKED_PAYMENT

    def test_tenant_not_blocked_while_still_within_grace_period(self, app):
        with app.app_context():
            settings = PlatformSettings.get_or_create()
            settings.dunning_grace_days = 3
            db.session.commit()

            tenant = _tenant()
            tenant.status = TenantStatus.ACTIVE
            billing = AdminBillingService()
            invoice = billing.create_invoice(tenant, amount_reais=50.0, due_date=date.today() - timedelta(days=1))
            billing.mark_overdue(invoice)

            summary = billing.process_dunning()

            assert summary["blocked"] == 0
            db.session.refresh(tenant)
            assert tenant.status == TenantStatus.ACTIVE
