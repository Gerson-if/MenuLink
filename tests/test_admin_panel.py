import re
from datetime import date, timedelta

from app.extensions import db
from app.models import Invoice, InvoiceStatus, Plan, Tenant, TenantStatus, User, UserSession


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

    def test_new_tenant_gets_starter_content(self, app, client, login_as):
        # Toda loja nova (criada pelo Super Admin ou por auto-cadastro,
        # ambos passam por AdminTenantService.create) já sai com um
        # cardápio de exemplo — ver app/services/starter_content_service.py.
        # Assim o lojista nunca cai num painel totalmente vazio.
        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get("/admin/lojistas/novo")
        csrf = _get_csrf(resp.get_data(as_text=True))
        client.post(
            "/admin/lojistas/novo",
            data={
                "name": "Pizzaria Starter", "email": "contato@starter.com",
                "phone": "", "whatsapp_number": "", "plan_id": "0",
                "owner_name": "Dono Starter", "owner_email": "dono@starter.com",
                "owner_password": "senha123", "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        with app.app_context():
            from app.models import Banner, Category, Product

            tenant = Tenant.query.filter_by(email="contato@starter.com").first()
            assert tenant is not None
            assert tenant.logo_path is not None

            categories = Category.query.filter_by(tenant_id=tenant.id).all()
            assert len(categories) == 4

            products = Product.query.filter_by(tenant_id=tenant.id).all()
            assert len(products) == 4
            assert all(p.tag == "Exemplo" for p in products)
            assert all(p.primary_image is not None for p in products)

            assert Banner.query.filter_by(tenant_id=tenant.id).count() == 1

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

    def test_super_admin_can_reset_owner_password(self, app, client, login_as):
        # A "sessão antiga do lojista" é criada direto no banco (não
        # navegando como o dono neste `client`) — logar como o dono e
        # DEPOIS como admin no mesmo client exigiria passar por /logout
        # entre os dois (current_user do Flask-Login fica cacheado em `g`
        # pelo resto do teste, ver test_delete_tenant_cascades_related_data),
        # e /logout já revoga a sessão sozinho (SessionService.
        # revoke_current_session) — o que "esconderia" se é a
        # REDEFINIÇÃO DE SENHA quem de fato revoga a sessão antiga.
        with app.app_context():
            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            owner_session = UserSession(
                user_id=owner.id, session_token="fake-token-para-teste",
                ip_address="127.0.0.1", user_agent="pytest",
            )
            db.session.add(owner_session)
            db.session.commit()
            owner_session_id = owner_session.id
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        login_as(client, "admin@cardapio.saas", "admin123")

        resp = client.get(f"/admin/lojistas/{tenant_id}")
        csrf = _get_csrf(resp.get_data(as_text=True))
        resp = client.post(
            f"/admin/lojistas/{tenant_id}/redefinir-senha-lojista",
            data={"new_password": "novaSenha456", "confirm_password": "novaSenha456", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "redefinida com sucesso" in resp.get_data(as_text=True)

        with app.app_context():
            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            assert owner.check_password("novaSenha456")
            assert not owner.check_password("lojista123")

            # a sessão que o lojista já tinha aberta foi encerrada
            refreshed_session = db.session.get(UserSession, owner_session_id)
            assert refreshed_session.revoked_at is not None

    def test_reset_owner_password_rejects_mismatched_confirmation(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant_id = Tenant.query.filter_by(slug="braseiro-cia").first().id

        resp = client.get(f"/admin/lojistas/{tenant_id}")
        csrf = _get_csrf(resp.get_data(as_text=True))
        resp = client.post(
            f"/admin/lojistas/{tenant_id}/redefinir-senha-lojista",
            data={"new_password": "novaSenha456", "confirm_password": "outraSenha789", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert "não coincidem" in resp.get_data(as_text=True)
        with app.app_context():
            owner = User.query.filter_by(email="lojista@braseiroecia.com.br").first()
            assert owner.check_password("lojista123")  # não mudou

    def test_lojista_cannot_reset_owner_password(self, client, login_as):
        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.post(
            "/admin/lojistas/1/redefinir-senha-lojista",
            data={"new_password": "x", "confirm_password": "x"},
        )
        assert resp.status_code == 403

    def test_delete_tenant_cascades_related_data(self, app, client, login_as):
        # cria loja descartável via service diretamente para simplificar
        from app.services.admin_tenant_service import AdminTenantService
        with app.app_context():
            tenant = AdminTenantService().create(
                name="Loja Descartável", email="descartavel@x.com", phone=None, whatsapp_number=None,
                plan_id=None, owner_name="Dono", owner_email="dono@descartavel.com", owner_password="senha123",
            )
            tenant_id = tenant.id

        # Regressão: o lojista precisa ter feito login pelo menos uma vez
        # (criando um UserSession, ver app/models/user_session.py) antes de
        # a loja ser excluída — sem cascade="all, delete-orphan" +
        # passive_deletes=True em User.sessions, apagar o User em cascata
        # (pela exclusão do Tenant) fazia o SQLAlchemy tentar um
        # UPDATE user_sessions SET user_id = NULL em vez de apagar a linha,
        # o que quebrava com IntegrityError (user_id é NOT NULL). Login e
        # logout no MESMO client (em vez de um segundo `app.test_client()`)
        # porque o `current_user` do Flask-Login é cacheado em `g`, que é
        # por CONTEXTO DE APLICAÇÃO — e a fixture `app` mantém um contexto
        # de aplicação aberto por todo o teste, então um segundo test
        # client "reaproveitaria" esse `g` e o login do admin logo depois
        # pareceria estar autenticado como o dono da loja.
        login_as(client, "dono@descartavel.com", "senha123")
        client.get("/logout")

        login_as(client, "admin@cardapio.saas", "admin123")
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

    def test_can_delete_unused_plan(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")

        from app.models import Plan as PlanModel

        with app.app_context():
            plan = PlanModel(name="Descartável", slug="descartavel-plano", price_cents=1000, billing_cycle="monthly")
            db.session.add(plan)
            db.session.commit()
            plan_id = plan.id

        resp = client.post(f"/admin/planos/{plan_id}/excluir", follow_redirects=True)
        assert "excluído com sucesso" in resp.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(PlanModel, plan_id) is None

    def test_cannot_delete_plan_in_use_by_tenant(self, app, client, login_as):
        login_as(client, "admin@cardapio.saas", "admin123")
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            plan_id = tenant.plan_id
            assert plan_id is not None

        resp = client.post(f"/admin/planos/{plan_id}/excluir", follow_redirects=True)
        assert "Não é possível excluir" in resp.get_data(as_text=True)
        with app.app_context():
            from app.models import Plan as PlanModel

            assert db.session.get(PlanModel, plan_id) is not None  # não foi excluído


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
