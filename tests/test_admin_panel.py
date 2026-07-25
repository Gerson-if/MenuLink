import re
from datetime import date, timedelta

from app.extensions import db
from app.models import Invoice, InvoiceStatus, Plan, Tenant, TenantStatus, User


def _get_csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return m.group(1) if m else None


class TestTenantManagement:
    def test_super_admin_can_create_tenant_with_owner(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/lojistas/novo")
        csrf = _get_csrf(resp.get_data(as_text=True))

        resp = client.post(
            "/admin/lojistas/novo",
            data={
                "name": "Pizzaria Bella",
                "email": "contato@bella.com",
                "phone": "",
                "whatsapp_number": "",
                "plan_id": "0",
                "owner_name": "Marco",
                "owner_email": "marco@bella.com",
                "owner_password": "senha123",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "criada com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            tenant = Tenant.query.filter_by(email="contato@bella.com").first()
            assert tenant is not None
            assert tenant.status == TenantStatus.TRIAL

            owner = User.query.filter_by(email="marco@bella.com").first()
            assert owner is not None
            assert owner.tenant_id == tenant.id
            assert owner.check_password("senha123")

    def test_duplicate_tenant_email_is_rejected(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/lojistas/novo")
        csrf = _get_csrf(resp.get_data(as_text=True))
        payload = {
            "name": "Duplicado", "email": "contato@braseiroecia.com.br",  # já existe (seed)
            "phone": "", "whatsapp_number": "", "plan_id": "0",
            "owner_name": "X", "owner_email": "novo-email@x.com", "owner_password": "senha123",
            "csrf_token": csrf,
        }
        resp = client.post("/admin/lojistas/novo", data=payload, follow_redirects=True)
        assert "Já existe uma loja cadastrada" in resp.get_data(as_text=True)

    def test_lojista_cannot_access_admin_tenant_routes(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/lojistas")
        assert resp.status_code == 403

    def test_search_and_filter_tenants(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/lojistas?q=Braseiro")
        assert "Braseiro" in resp.get_data(as_text=True)

        resp = client.get("/admin/lojistas?status=active")
        body = resp.get_data(as_text=True)
        assert "Braseiro" in body  # seed tenant é ACTIVE

    def test_edit_tenant_updates_fields(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        resp = client.get(f"/admin/lojistas/{tenant_id}/editar")
        csrf = _get_csrf(resp.get_data(as_text=True))
        resp = client.post(
            f"/admin/lojistas/{tenant_id}/editar",
            data={
                "name": "Braseiro & Cia Grill", "email": "contato@braseiroecia.com.br",
                "phone": "6733334444", "whatsapp_number": "5567999998888",
                "plan_id": "0", "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "atualizados" in resp.get_data(as_text=True)
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            assert tenant.name == "Braseiro & Cia Grill"
            assert tenant.phone == "6733334444"

    def test_delete_tenant_cascades_related_data(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        # cria loja descartável via service diretamente para simplificar
        from app.services.admin_tenant_service import AdminTenantService
        with app.app_context():
            tenant = AdminTenantService().create(
                name="Loja Descartável", email="descartavel@x.com", phone=None, whatsapp_number=None,
                plan_id=None, owner_name="Dono", owner_email="dono@descartavel.com", owner_password="senha123",
            )
            tenant_id = tenant.id

        resp = client.post(f"/admin/lojistas/{tenant_id}/excluir", follow_redirects=True)
        assert "excluídos permanentemente" in resp.get_data(as_text=True)

        with app.app_context():
            assert db.session.get(Tenant, tenant_id) is None
            assert User.query.filter_by(email="dono@descartavel.com").first() is None


class TestTenantStatusTransitions:
    def test_block_tenant_prevents_login_and_activate_restores_it(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        client.post(f"/admin/lojistas/{tenant_id}/bloquear", data={"reason": "Fatura de julho em aberto"})

        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            assert tenant.status == TenantStatus.BLOCKED_PAYMENT
            assert tenant.blocked_reason == "Fatura de julho em aberto"

        client.get("/logout")
        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        # BLOCKED_PAYMENT permite login, mas só chega na página de
        # regularização de pendências — nunca no painel normal.
        assert "Acesso bloqueado por pend" in resp.get_data(as_text=True)
        assert "Categorias" not in resp.get_data(as_text=True)

        client.get("/logout")
        login_as(client, "admin@cardapio.saas", "admin123")
        client.post(f"/admin/lojistas/{tenant_id}/ativar")

        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            assert tenant.status == TenantStatus.ACTIVE
            assert tenant.blocked_reason is None

        client.get("/logout")
        resp = login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        assert "Categorias" in resp.get_data(as_text=True)  # conseguiu entrar no dashboard

    def test_suspend_and_cancel_transitions(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        client.post(f"/admin/lojistas/{tenant_id}/suspender", data={"reason": "Revisão de cadastro"})
        with app.app_context():
            assert db.session.get(Tenant, tenant_id).status == TenantStatus.SUSPENDED

        client.post(f"/admin/lojistas/{tenant_id}/cancelar", data={"reason": "Encerramento voluntário"})
        with app.app_context():
            tenant = db.session.get(Tenant, tenant_id)
            assert tenant.status == TenantStatus.CANCELED
            assert tenant.blocked_reason == "Encerramento voluntário"


class TestPlanManagement:
    def test_create_and_toggle_plan(self, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/planos/novo")
        csrf = _get_csrf(resp.get_data(as_text=True))
        resp = client.post(
            "/admin/planos/novo",
            data={
                "name": "Premium", "description": "Plano completo", "price": "99.90",
                "billing_cycle": "monthly", "max_categories": "", "max_products": "",
                "max_images_per_product": "10", "csrf_token": csrf,
            },
            follow_redirects=True,
        )
        assert "Plano criado com sucesso" in resp.get_data(as_text=True)

        from app.models import Plan as PlanModel

        plan = PlanModel.query.filter_by(name="Premium").first()
        assert plan.price_cents == 9990
        assert plan.max_categories is None  # ilimitado

        resp = client.post(f"/admin/planos/{plan.id}/alternar", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Inativo" in resp.get_data(as_text=True)

    def test_lojista_cannot_access_plan_routes(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/admin/planos")
        assert resp.status_code == 403


class TestBilling:
    def test_create_invoice_and_mark_paid_unblocks_tenant(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.status = TenantStatus.BLOCKED_PAYMENT
            tenant.blocked_reason = "Fatura vencida"
            db.session.commit()
            tenant_id = tenant.id

        due = (date.today() + timedelta(days=10)).isoformat()
        resp = client.post(
            f"/admin/lojistas/{tenant_id}/faturas/nova",
            data={"amount": "49.90", "due_date": due},
            follow_redirects=True,
        )
        assert "Fatura lançada com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            invoice = Invoice.query.filter_by(tenant_id=tenant_id).first()
            assert invoice is not None
            assert invoice.amount_cents == 4990
            assert invoice.status == InvoiceStatus.PENDING
            invoice_id = invoice.id

        resp = client.post(f"/admin/faturas/{invoice_id}/pagar", follow_redirects=True)
        assert "acesso foi liberado" in resp.get_data(as_text=True)

        with app.app_context():
            invoice = db.session.get(Invoice, invoice_id)
            assert invoice.status == InvoiceStatus.PAID
            assert invoice.paid_at is not None

            tenant = db.session.get(Tenant, tenant_id)
            assert tenant.status == TenantStatus.ACTIVE
            assert tenant.blocked_reason is None

    def test_cancel_invoice(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        due = (date.today() + timedelta(days=5)).isoformat()
        client.post(f"/admin/lojistas/{tenant_id}/faturas/nova", data={"amount": "10.00", "due_date": due})

        with app.app_context():
            invoice = Invoice.query.filter_by(tenant_id=tenant_id).order_by(Invoice.id.desc()).first()
            invoice_id = invoice.id

        resp = client.post(f"/admin/faturas/{invoice_id}/cancelar", follow_redirects=True)
        assert "Fatura cancelada" in resp.get_data(as_text=True)

        with app.app_context():
            assert db.session.get(Invoice, invoice_id).status == InvoiceStatus.CANCELED
