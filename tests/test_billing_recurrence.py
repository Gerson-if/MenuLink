"""
Recorrência de faturas: ciclo de cobrança por assinatura (independente
do Plan) e geração automática da próxima fatura, ver
AdminBillingService.update_subscription_cycle / generate_next_invoice.
"""

from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import BillingCycle, Invoice, InvoiceStatus, Plan, Subscription, SubscriptionStatus, Tenant
from app.services.admin_billing_service import AdminBillingService, BillingError, _add_cycle


def _tenant():
    return Tenant.query.filter_by(slug="braseiro-cia").first()


class TestAddCycle:
    def test_monthly_advances_calendar_correct(self):
        assert _add_cycle(date(2026, 1, 31), BillingCycle.MONTHLY) == date(2026, 2, 28)
        assert _add_cycle(date(2026, 1, 15), BillingCycle.MONTHLY) == date(2026, 2, 15)
        assert _add_cycle(date(2026, 12, 10), BillingCycle.MONTHLY) == date(2027, 1, 10)

    def test_yearly_advances_one_year(self):
        assert _add_cycle(date(2026, 3, 10), BillingCycle.YEARLY) == date(2027, 3, 10)

    def test_yearly_leap_day_falls_back_to_28(self):
        assert _add_cycle(date(2024, 2, 29), BillingCycle.YEARLY) == date(2025, 2, 28)


class TestGetOrCreateSubscription:
    def test_new_subscription_copies_cycle_from_plan(self, app):
        with app.app_context():
            tenant = _tenant()
            plan = Plan(name="Anual Teste", slug="anual-teste", price_cents=9990, billing_cycle=BillingCycle.YEARLY)
            db.session.add(plan)
            db.session.flush()
            tenant.plan_id = plan.id
            db.session.commit()

            subscription = AdminBillingService().get_or_create_subscription(tenant)

            assert subscription.billing_cycle == BillingCycle.YEARLY
            assert subscription.current_period_end == _add_cycle(date.today(), BillingCycle.YEARLY)


class TestUpdateSubscriptionCycle:
    def test_overrides_cycle_without_touching_plan(self, app):
        with app.app_context():
            tenant = _tenant()
            service = AdminBillingService()
            subscription = service.get_or_create_subscription(tenant)
            assert subscription.billing_cycle == BillingCycle.MONTHLY

            service.update_subscription_cycle(subscription, BillingCycle.YEARLY)

            assert subscription.billing_cycle == BillingCycle.YEARLY
            assert subscription.plan.billing_cycle == BillingCycle.MONTHLY  # plano não muda

    def test_route_updates_cycle(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = _tenant().id

        resp = client.post(
            f"/admin/lojistas/{tenant_id}/assinatura/ciclo", data={"billing_cycle": "yearly"}, follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            subscription = AdminBillingService().get_subscription_for_tenant(tenant_id)
            assert subscription.billing_cycle == BillingCycle.YEARLY


class TestGenerateNextInvoice:
    def test_generates_from_plan_price_and_cycle(self, app):
        with app.app_context():
            tenant = _tenant()
            service = AdminBillingService()
            subscription = service.get_or_create_subscription(tenant)
            expected_due = subscription.current_period_end

            invoice = service.generate_next_invoice(subscription)

            assert invoice.amount_cents == subscription.plan.price_cents
            assert invoice.due_date == _add_cycle(expected_due, BillingCycle.MONTHLY)
            assert invoice.status == InvoiceStatus.PENDING
            assert subscription.current_period_end == invoice.due_date

    def test_refuses_when_open_invoice_exists(self, app):
        with app.app_context():
            tenant = _tenant()
            service = AdminBillingService()
            subscription = service.get_or_create_subscription(tenant)
            service.create_invoice(tenant, amount_reais=50.0, due_date=date.today() + timedelta(days=5))

            with pytest.raises(BillingError, match="fatura em aberto"):
                service.generate_next_invoice(subscription)

    def test_refuses_when_plan_price_is_zero(self, app):
        with app.app_context():
            tenant = _tenant()
            free_plan = Plan(name="Grátis", slug="gratis-teste", price_cents=0, billing_cycle=BillingCycle.MONTHLY)
            db.session.add(free_plan)
            db.session.flush()
            tenant.plan_id = free_plan.id
            db.session.commit()

            service = AdminBillingService()
            subscription = service.get_or_create_subscription(tenant)

            with pytest.raises(BillingError, match="não tem um preço definido"):
                service.generate_next_invoice(subscription)

    def test_route_generates_invoice(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = _tenant().id

        resp = client.post(
            f"/admin/lojistas/{tenant_id}/assinatura/gerar-proxima-fatura", follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            invoices = AdminBillingService().list_invoices_for_tenant(tenant_id)
            assert len(invoices) == 1
            assert invoices[0].status == InvoiceStatus.PENDING

    def test_route_flashes_error_on_duplicate_open_invoice(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = _tenant().id

        client.post(f"/admin/lojistas/{tenant_id}/assinatura/gerar-proxima-fatura", follow_redirects=True)
        resp = client.post(f"/admin/lojistas/{tenant_id}/assinatura/gerar-proxima-fatura", follow_redirects=True)

        assert "fatura em aberto" in resp.get_data(as_text=True)


class TestGenerateAsaasChargeAdditionalValidations:
    def test_refuses_past_due_date(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key"
            tenant = _tenant()
            service = AdminBillingService()
            invoice = service.create_invoice(tenant, amount_reais=50.0, due_date=date.today() - timedelta(days=1))

            with pytest.raises(BillingError, match="já passou"):
                service.generate_asaas_charge(invoice)

    def test_refuses_when_tenant_has_no_email(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key"
            tenant = _tenant()
            tenant.email = ""
            db.session.commit()
            service = AdminBillingService()
            invoice = service.create_invoice(tenant, amount_reais=50.0, due_date=date.today() + timedelta(days=5))

            with pytest.raises(BillingError, match="e-mail cadastrado"):
                service.generate_asaas_charge(invoice)

    def test_refuses_for_paid_invoice(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key"
            tenant = _tenant()
            service = AdminBillingService()
            invoice = service.create_invoice(tenant, amount_reais=50.0, due_date=date.today() + timedelta(days=5))
            service.mark_paid(invoice)

            with pytest.raises(BillingError, match="pendente ou vencida"):
                service.generate_asaas_charge(invoice)


class TestInvoiceFormValidation:
    def test_rejects_amount_above_max(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = _tenant().id

        resp = client.post(
            f"/admin/lojistas/{tenant_id}/faturas/nova",
            data={"amount": "999999.99", "due_date": (date.today() + timedelta(days=10)).isoformat()},
            follow_redirects=True,
        )
        assert "R$ 100.000,00" in resp.get_data(as_text=True)

    def test_rejects_due_date_too_far_in_future(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = _tenant().id

        resp = client.post(
            f"/admin/lojistas/{tenant_id}/faturas/nova",
            data={"amount": "50.00", "due_date": (date.today() + timedelta(days=1000)).isoformat()},
            follow_redirects=True,
        )
        assert "muito distante" in resp.get_data(as_text=True)
