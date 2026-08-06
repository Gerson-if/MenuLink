"""
AdminMetricsService (app/services/admin_metrics_service.py) e o dashboard
do Super Admin (app/controllers/admin/dashboard.py) — métricas de negócio
agregadas do sistema inteiro (MRR, receita cobrada, crescimento de
lojistas, distribuição por status/plano).
"""

from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    BillingCycle, Invoice, InvoiceStatus, Plan, Subscription, SubscriptionStatus, Tenant, TenantStatus,
)
from app.services.admin_metrics_service import AdminMetricsService


def _make_active_subscription(tenant, plan, *, billing_cycle=BillingCycle.MONTHLY):
    subscription = Subscription(
        tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
        billing_cycle=billing_cycle,
        current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
    )
    db.session.add(subscription)
    db.session.flush()
    return subscription


def _make_paid_invoice(tenant, subscription, amount_cents, *, paid_at=None):
    invoice = Invoice(
        tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=amount_cents,
        status=InvoiceStatus.PAID, due_date=date.today(), paid_at=paid_at or datetime.now(timezone.utc),
    )
    db.session.add(invoice)
    db.session.flush()
    return invoice


class TestMrrCents:
    def test_sums_monthly_plan_price_for_active_tenant_and_subscription(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.ACTIVE
            _make_active_subscription(tenant, plan)
            db.session.commit()

            assert AdminMetricsService().mrr_cents() == plan.price_cents

    def test_yearly_subscription_is_normalized_to_monthly(self, app):
        with app.app_context():
            plan = Plan(name="Anual", slug="anual-teste", price_cents=120000, billing_cycle=BillingCycle.YEARLY)
            db.session.add(plan)
            db.session.flush()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.ACTIVE
            tenant.plan_id = plan.id
            _make_active_subscription(tenant, plan, billing_cycle=BillingCycle.YEARLY)
            db.session.commit()

            assert AdminMetricsService().mrr_cents() == 120000 // 12

    def test_ignores_subscription_of_non_active_tenant(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.TRIAL
            _make_active_subscription(tenant, plan)
            db.session.commit()

            assert AdminMetricsService().mrr_cents() == 0

    def test_ignores_canceled_subscription(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.ACTIVE
            subscription = _make_active_subscription(tenant, plan)
            subscription.status = SubscriptionStatus.CANCELED
            db.session.commit()

            assert AdminMetricsService().mrr_cents() == 0


class TestOpenInvoicesCents:
    def test_sums_pending_and_overdue_but_not_paid_or_canceled(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            subscription = _make_active_subscription(tenant, plan)
            db.session.add_all([
                Invoice(tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=1000, status=InvoiceStatus.PENDING, due_date=date.today()),
                Invoice(tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=2000, status=InvoiceStatus.OVERDUE, due_date=date.today()),
                Invoice(tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=4000, status=InvoiceStatus.PAID, due_date=date.today(), paid_at=datetime.now(timezone.utc)),
                Invoice(tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=8000, status=InvoiceStatus.CANCELED, due_date=date.today()),
            ])
            db.session.commit()

            assert AdminMetricsService().open_invoices_cents() == 3000


class TestTenantKpis:
    def test_new_this_month_counts_only_tenants_created_this_month(self, app):
        with app.app_context():
            plan = Plan.query.first()
            old_tenant = Tenant(
                name="Loja Antiga", slug="loja-antiga-kpi", email="antiga@x.com",
                plan_id=plan.id, status=TenantStatus.ACTIVE,
            )
            db.session.add(old_tenant)
            db.session.commit()
            # Força created_at pra fora do mês corrente (o default é "agora").
            db.session.query(Tenant).filter_by(id=old_tenant.id).update(
                {"created_at": datetime.now(timezone.utc) - timedelta(days=400)}
            )
            db.session.commit()

            kpis = AdminMetricsService().tenant_kpis()
            # braseiro-cia (fixture) + old_tenant existem, mas só braseiro-cia
            # (criado "agora" pela fixture) conta como "este mês".
            assert kpis["new_this_month"] == 1
            assert kpis["total_tenants"] == 2


class TestTenantGrowthSeries:
    def test_counts_tenant_in_the_month_it_was_created(self, app):
        with app.app_context():
            series = AdminMetricsService().tenant_growth_series(months=3)
            assert len(series) == 3
            assert series[-1]["count"] == 1  # braseiro-cia, criado agora pela fixture
            assert series[0]["count"] == 0
            assert series[1]["count"] == 0


class TestRevenueSeries:
    def test_sums_paid_invoices_by_month_of_paid_at(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            subscription = _make_active_subscription(tenant, plan)
            _make_paid_invoice(tenant, subscription, 5000)

            series = AdminMetricsService().revenue_series(months=3)
            assert series[-1]["revenue_cents"] == 5000
            assert series[0]["revenue_cents"] == 0

    def test_ignores_unpaid_invoices(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            subscription = _make_active_subscription(tenant, plan)
            db.session.add(Invoice(
                tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=9999,
                status=InvoiceStatus.PENDING, due_date=date.today(),
            ))
            db.session.commit()

            series = AdminMetricsService().revenue_series(months=3)
            assert sum(m["revenue_cents"] for m in series) == 0


class TestTenantsByStatus:
    def test_always_includes_all_five_statuses(self, app):
        with app.app_context():
            rows = AdminMetricsService().tenants_by_status()
            labels = [r["status"] for r in rows]
            assert labels == ["Em teste", "Ativa", "Bloqueada", "Suspensa", "Cancelada"]
            active_row = next(r for r in rows if r["status"] == "Ativa")
            assert active_row["count"] == 1  # braseiro-cia (fixture, ACTIVE)


class TestTenantsByPlan:
    def test_only_counts_active_tenants(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.TRIAL
            db.session.commit()

            assert AdminMetricsService().tenants_by_plan() == []

    def test_counts_active_tenant_under_its_plan_name(self, app):
        with app.app_context():
            plan = Plan.query.first()
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.ACTIVE
            db.session.commit()

            rows = AdminMetricsService().tenants_by_plan()
            assert rows == [{"plan": plan.name, "count": 1}]


class TestDashboardRoute:
    def test_requires_super_admin(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/dashboard")
        assert resp.status_code == 403

    def test_renders_kpis_and_chart_canvases(self, app, client, login_as):
        with app.app_context():
            plan = Plan.query.first()
            price_cents = plan.price_cents
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.ACTIVE
            subscription = _make_active_subscription(tenant, plan)
            _make_paid_invoice(tenant, subscription, price_cents)
            db.session.commit()

        login_as(client, "admin@cardapio.saas", "admin123")
        resp = client.get("/admin/dashboard")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "MRR" in html
        assert f"R$ {price_cents / 100:.2f}" in html
        assert 'id="tenantGrowthChart"' in html
        assert 'id="revenueChart"' in html
        assert 'id="statusChart"' in html
        assert 'id="planChart"' in html
