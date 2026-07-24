"""
Cobre os ajustes desta rodada: páginas legais (Termos/Privacidade)
linkadas no rodapé da landing, e o reforço de isolamento/concorrência de
clientes (constraint única de telefone por loja).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Customer, Tenant


class TestLegalPages:
    def test_landing_footer_links_to_terms_and_privacy(self, client):
        resp = client.get("/")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert '/termos-de-uso' in body
        assert '/politica-de-privacidade' in body

    def test_terms_of_use_page_renders(self, client):
        resp = client.get("/termos-de-uso")
        assert resp.status_code == 200
        assert "Termos de Uso" in resp.get_data(as_text=True)

    def test_privacy_policy_page_renders(self, client):
        resp = client.get("/politica-de-privacidade")
        assert resp.status_code == 200
        assert "Política de Privacidade" in resp.get_data(as_text=True)
        assert "LGPD" in resp.get_data(as_text=True)


class TestCustomerPhoneIsolationAndConcurrency:
    def test_duplicate_phone_for_same_tenant_is_rejected_at_db_level(self, app):
        """
        Garante no banco (não só na checagem da aplicação) que duas linhas
        de Customer não podem coexistir com o mesmo (tenant_id, phone) —
        a defesa contra a corrida de dois checkouts concorrentes com o
        mesmo telefone (duplo clique, duas abas, retry de rede).
        """
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            db.session.add(Customer(tenant_id=tenant.id, name="Cliente A", phone="(67) 99999-0000"))
            db.session.commit()

            db.session.add(Customer(tenant_id=tenant.id, name="Cliente B (duplicata)", phone="(67) 99999-0000"))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_same_phone_allowed_across_different_tenants(self, app):
        """O mesmo telefone pode ser cliente de lojas diferentes — a
        constraint é por (tenant_id, phone), nunca só por phone."""
        from app.models import BillingCycle, Plan, TenantStatus

        with app.app_context():
            tenant_a = Tenant.query.filter_by(slug="braseiro-cia").first()
            plan = Plan.query.first()
            tenant_b = Tenant(
                name="Outra Loja Concorrencia", slug="outra-loja-concorrencia",
                email="concorrencia@x.com", plan_id=plan.id, status=TenantStatus.ACTIVE,
            )
            db.session.add(tenant_b)
            db.session.flush()

            db.session.add(Customer(tenant_id=tenant_a.id, name="Cliente Compartilhado", phone="(67) 98888-1111"))
            db.session.add(Customer(tenant_id=tenant_b.id, name="Cliente Compartilhado", phone="(67) 98888-1111"))
            db.session.commit()  # não deve levantar IntegrityError

            count = Customer.query.filter_by(phone="(67) 98888-1111").count()
            assert count == 2

    def test_multiple_null_phones_are_allowed(self, app):
        """Clientes sem telefone (phone NULL) não devem colidir entre si —
        comportamento padrão de UNIQUE com NULL em Postgres/SQLite."""
        with app.app_context():
            tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
            db.session.add(Customer(tenant_id=tenant.id, name="Sem Telefone 1", phone=None))
            db.session.add(Customer(tenant_id=tenant.id, name="Sem Telefone 2", phone=None))
            db.session.commit()  # não deve levantar IntegrityError

            assert Customer.query.filter_by(tenant_id=tenant.id, phone=None).count() == 2
