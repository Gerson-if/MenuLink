import re

from app.extensions import db
from app.models import BillingCycle, Plan, Tenant


def _csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return m.group(1) if m else None


class TestNewTenantsNeverGetNullPlan:
    def test_self_registration_falls_back_to_cheapest_active_plan(self, app, client):
        with app.app_context():
            # "Básico" (o plano do conftest) custa R$ 49,90 — cria um mais
            # barato pra confirmar que a escolha é mesmo por preço, não
            # "o primeiro que existir".
            cheaper = Plan(name="Starter", slug="starter", price_cents=1990, billing_cycle=BillingCycle.MONTHLY, is_active=True)
            db.session.add(cheaper)
            db.session.commit()

        resp = client.get("/cadastro")
        csrf = _csrf(resp.get_data(as_text=True))
        client.post(
            "/cadastro",
            data={
                "owner_name": "Dona Sem Plano", "whatsapp_number": "11988887777",
                "store_name": "Loja Sem Plano Explicito", "slug": "loja-sem-plano-explicito",
                "email": "semplano@example.com", "password": "senha123",
                "password_confirm": "senha123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="loja-sem-plano-explicito").first()
            assert tenant is not None
            assert tenant.plan_id is not None
            assert tenant.plan.slug == "starter"

    def test_admin_create_without_choosing_plan_also_falls_back(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/lojistas/novo")
        csrf = _csrf(resp.get_data(as_text=True))
        client.post(
            "/admin/lojistas/novo",
            data={
                "name": "Loja Admin Sem Plano", "email": "adminsemplano@example.com",
                "phone": "", "whatsapp_number": "", "plan_id": "0",
                "owner_name": "Dono Admin", "owner_email": "donoadmin@example.com",
                "owner_password": "senha123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        with app.app_context():
            tenant = Tenant.query.filter_by(email="adminsemplano@example.com").first()
            assert tenant is not None
            assert tenant.plan_id is not None


class TestLojistaSelfServicePlanChange:
    def test_lojista_can_change_to_another_active_plan(self, app, client, login_as):
        with app.app_context():
            plan = Plan(name="Pro", slug="pro", price_cents=9990, billing_cycle=BillingCycle.MONTHLY, is_active=True)
            db.session.add(plan)
            db.session.commit()
            plan_id = plan.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/assinatura/plano",
            data={"plan_id": plan_id},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "alterado" in resp.get_data(as_text=True)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.plan_id == plan_id

    def test_cannot_switch_to_an_inactive_plan(self, app, client, login_as):
        with app.app_context():
            inactive_plan = Plan(name="Descontinuado", slug="descontinuado", price_cents=500, billing_cycle=BillingCycle.MONTHLY, is_active=False)
            db.session.add(inactive_plan)
            db.session.commit()
            plan_id = inactive_plan.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/painel/configuracoes/assinatura/plano",
            data={"plan_id": plan_id},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "indisponível" in resp.get_data(as_text=True)

        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            assert tenant.plan_id != plan_id

    def test_subscription_page_lists_available_plans_and_marks_current(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        html = client.get("/painel/configuracoes/assinatura").get_data(as_text=True)
        assert "Trocar de plano" in html
        assert "Atual" in html
