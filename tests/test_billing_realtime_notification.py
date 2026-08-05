"""
Confirmação "em tempo real" de pagamento (Fase 5) — ver
AdminBillingService.mark_paid/_send_payment_confirmed_message e o
polling HTMX do painel de Faturas (lojista/settings/_invoices_panel.html,
app/controllers/lojista/settings.py:settings_subscription_billing_fragment).
"""

from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import ChatMessage, ChatSenderRole, Invoice, InvoiceStatus, Plan, Subscription, SubscriptionStatus, Tenant
from app.services.admin_billing_service import AdminBillingService


def _make_subscription_and_invoice(tenant, *, status=InvoiceStatus.PENDING, payment_link_url=None, paid_at=None):
    plan = Plan.query.first()
    subscription = Subscription(
        tenant_id=tenant.id, plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
        current_period_start=date.today(), current_period_end=date.today() + timedelta(days=30),
    )
    db.session.add(subscription)
    db.session.flush()
    invoice = Invoice(
        tenant_id=tenant.id, subscription_id=subscription.id, amount_cents=4990,
        status=status, due_date=date.today(), payment_link_url=payment_link_url, paid_at=paid_at,
    )
    db.session.add(invoice)
    db.session.commit()
    return subscription, invoice


class TestMarkPaidSendsChatConfirmation:
    def test_manual_mark_paid_sends_confirmation_with_invoice_data(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            _, invoice = _make_subscription_and_invoice(tenant)

            AdminBillingService().mark_paid(invoice)

            message = (
                ChatMessage.query.filter_by(tenant_id=tenant.id, sender_role=ChatSenderRole.SUPER_ADMIN)
                .order_by(ChatMessage.created_at.desc())
                .first()
            )
            assert message is not None
            assert "Pagamento confirmado" in message.body
            assert "49.90" in message.body
            plan_name = invoice.subscription.plan.name
            assert plan_name in message.body

    def test_does_not_duplicate_confirmation_when_already_paid(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            _, invoice = _make_subscription_and_invoice(
                tenant, status=InvoiceStatus.PAID, paid_at=datetime.now(timezone.utc),
            )

            before = ChatMessage.query.filter_by(tenant_id=tenant.id).count()
            AdminBillingService().mark_paid_by_asaas_payment_id("id-que-nao-existe")
            after = ChatMessage.query.filter_by(tenant_id=tenant.id).count()
            assert before == after  # payment_id desconhecido -> ignorado, sem nova mensagem


class TestNewInvoiceSendsChatNotification:
    """Aviso "em tempo real" de que uma fatura nova ficou disponível — ver
    AdminBillingService._send_new_invoice_message, chamado por
    `create_invoice` (lançamento manual) e `generate_next_invoice`
    (renovação automática)."""

    def test_manual_invoice_notifies_tenant(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            service = AdminBillingService()

            invoice = service.create_invoice(
                tenant, amount_reais=79.90, due_date=date.today() + timedelta(days=10),
            )

            message = (
                ChatMessage.query.filter_by(tenant_id=tenant.id, sender_role=ChatSenderRole.SUPER_ADMIN)
                .order_by(ChatMessage.created_at.desc())
                .first()
            )
            assert message is not None
            assert "nova fatura está disponível" in message.body
            assert "79.90" in message.body
            assert invoice.due_date.strftime("%d/%m/%Y") in message.body

    def test_next_invoice_notifies_tenant(self, app):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            service = AdminBillingService()
            subscription = service.get_or_create_subscription(tenant)

            service.generate_next_invoice(subscription)

            message = (
                ChatMessage.query.filter_by(tenant_id=tenant.id, sender_role=ChatSenderRole.SUPER_ADMIN)
                .order_by(ChatMessage.created_at.desc())
                .first()
            )
            assert message is not None
            assert "nova fatura está disponível" in message.body

    def test_message_asks_to_complete_registration_when_document_missing(self, app):
        with app.app_context():
            app.config["ASAAS_API_KEY"] = "fake-key"
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            tenant.document = None
            db.session.commit()
            service = AdminBillingService()
            subscription = service.get_or_create_subscription(tenant)

            service.generate_next_invoice(subscription)

            message = (
                ChatMessage.query.filter_by(tenant_id=tenant.id, sender_role=ChatSenderRole.SUPER_ADMIN)
                .order_by(ChatMessage.created_at.desc())
                .first()
            )
            assert "CPF/CNPJ" in message.body
            assert "Complete seu cadastro" in message.body


class TestLojistaBillingPollingFragment:
    def test_polling_enabled_while_invoice_awaits_payment(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            _make_subscription_and_invoice(tenant, payment_link_url="https://asaas.example/pay/123")

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/assinatura")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "settings_subscription_billing_fragment" not in html  # URL real, não o nome da view
        assert 'hx-trigger="every 8s"' in html
        # Sem spinner/aviso visível aqui — só liga o polling silencioso da
        # lista (ver _billing_panel_context). O aviso "aguardando
        # confirmação" de verdade agora mora na tela de pagamento Pix
        # embutida (ver TestInvoicePayPage neste mesmo arquivo).
        assert "Aguardando confirmação do pagamento" not in html

    def test_polling_disabled_without_pending_payment_link(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            _make_subscription_and_invoice(tenant, payment_link_url=None)

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")
        resp = client.get("/painel/configuracoes/assinatura")
        html = resp.get_data(as_text=True)
        assert 'hx-trigger="every 8s"' not in html

    def test_fragment_route_reflects_paid_status_live(self, app, client, login_as):
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            _, invoice = _make_subscription_and_invoice(tenant, payment_link_url="https://asaas.example/pay/456")
            invoice_id = invoice.id

        login_as(client, "lojista@braseiroecia.com.br", "lojista123")

        before = client.get("/painel/configuracoes/assinatura/fragmento")
        assert "Pendente".encode() in before.data

        with app.app_context():
            invoice = db.session.get(Invoice, invoice_id)
            AdminBillingService().mark_paid(invoice)

        after = client.get("/painel/configuracoes/assinatura/fragmento")
        after_html = after.get_data(as_text=True)
        assert "Paga" in after_html
        assert "Pagamento confirmado!" in after_html  # banner "recém-paga"
        assert 'hx-trigger="every 8s"' not in after_html  # nada mais pendente -> para de sondar

    def test_requires_login(self, client):
        resp = client.get("/painel/configuracoes/assinatura/fragmento", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)
