"""
Regras de negócio de gestão de lojistas pelo Super Administrador.

Transições de status são centralizadas em `change_status`, que também
cuida dos efeitos colaterais esperados (ex: bloquear por inadimplência
registra o motivo e a data; liberar acesso limpa o motivo do bloqueio).
"""

from datetime import datetime, timezone

from app.extensions import db
from app.models import Tenant, TenantStatus, User, UserRole
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.utils.slugs import generate_unique_slug


class TenantAdminError(Exception):
    """Erro de regra de negócio, com mensagem segura para exibir ao Super Admin."""


class AdminTenantService:
    def __init__(self):
        self.repo = TenantRepository()
        self.user_repo = UserRepository()

    # --- Consulta ---
    def search(self, term: str = "", status: str = ""):
        return self.repo.search(term=term, status=status)

    def get_or_404(self, tenant_id: int) -> Tenant:
        from flask import abort

        tenant = self.repo.get_by_id(tenant_id)
        if tenant is None:
            abort(404)
        return tenant

    # --- CRUD ---
    def create(self, *, name: str, email: str, phone: str, whatsapp_number: str,
               plan_id: int | None, owner_name: str, owner_email: str, owner_password: str,
               slug: str | None = None) -> Tenant:
        if self.repo.get_by_email(email):
            raise TenantAdminError("Já existe uma loja cadastrada com este e-mail.")
        if self.user_repo.get_by_email(owner_email):
            raise TenantAdminError("Já existe um usuário cadastrado com este e-mail de login.")

        slug = generate_unique_slug(slug or name, self.repo.get_by_slug)

        tenant = Tenant(
            name=name.strip(),
            slug=slug,
            email=email.lower().strip(),
            phone=phone or None,
            whatsapp_number=whatsapp_number or None,
            plan_id=plan_id,
            status=TenantStatus.TRIAL,
        )
        db.session.add(tenant)
        db.session.flush()

        owner = User(
            tenant_id=tenant.id,
            name=owner_name.strip(),
            email=owner_email.lower().strip(),
            role=UserRole.OWNER,
        )
        owner.set_password(owner_password)
        db.session.add(owner)

        db.session.commit()

        # Conteúdo de exemplo (categorias, produtos, banner, logo) pra
        # loja não começar 100% vazia — ver starter_content_service.py.
        # Chamado DEPOIS do commit acima de propósito: essa é uma etapa
        # "de cortesia", nunca deve impedir a criação da loja em si (o
        # próprio seed_starter_content já isola falhas com try/rollback,
        # mas rodar depois do commit é uma segunda garantia).
        from app.services.starter_content_service import seed_starter_content

        seed_starter_content(tenant)

        return tenant

    def update(self, tenant: Tenant, *, name: str, email: str, phone: str,
               whatsapp_number: str, plan_id: int | None) -> Tenant:
        existing = self.repo.get_by_email(email)
        if existing and existing.id != tenant.id:
            raise TenantAdminError("Já existe uma loja cadastrada com este e-mail.")

        if name.strip() != tenant.name:
            tenant.slug = generate_unique_slug(name, self.repo.get_by_slug, current_id=tenant.id)
        tenant.name = name.strip()
        tenant.email = email.lower().strip()
        tenant.phone = phone or None
        tenant.whatsapp_number = whatsapp_number or None
        tenant.plan_id = plan_id
        db.session.commit()
        return tenant

    def set_owner_password(self, tenant: Tenant, new_password: str) -> User:
        """Super Admin redefine a senha do lojista (dono da loja) — ex:
        ele esqueceu a senha e não tem e-mail configurado pra
        recuperação, ou pediu suporte por outro canal. Ao contrário da
        troca de senha pelo próprio usuário (AuthService.change_password),
        não exige a senha atual — quem está autenticado aqui é o Super
        Admin, não o lojista.

        Encerra todas as sessões abertas do lojista (mesmo motivo do
        AuthService.change_password: uma sessão já aberta em outro
        lugar não deve continuar valendo depois que a senha muda por
        baixo dela)."""
        owner = tenant.owner
        if owner is None:
            raise TenantAdminError("Esta loja não tem um usuário lojista (dono) cadastrado.")

        owner.set_password(new_password)
        db.session.commit()

        from app.services.session_service import SessionService

        SessionService().revoke_all_other_sessions(owner.id)
        return owner

    def delete(self, tenant: Tenant) -> None:
        # O cascade definido nos relacionamentos do model (categorias,
        # produtos, pedidos, clientes, usuários, assinaturas, faturas)
        # cuida de remover todos os dados da loja junto.
        db.session.delete(tenant)
        db.session.commit()

    # --- Transições de status ---
    def activate(self, tenant: Tenant) -> Tenant:
        tenant.status = TenantStatus.ACTIVE
        tenant.blocked_reason = None
        tenant.blocked_at = None
        db.session.commit()
        return tenant

    def suspend(self, tenant: Tenant, reason: str | None = None) -> Tenant:
        tenant.status = TenantStatus.SUSPENDED
        tenant.blocked_reason = reason or "Suspensa pelo Super Administrador."
        tenant.blocked_at = datetime.now(timezone.utc)
        db.session.commit()
        return tenant

    def block_for_payment(self, tenant: Tenant, reason: str | None = None) -> Tenant:
        tenant.status = TenantStatus.BLOCKED_PAYMENT
        tenant.blocked_reason = reason or "Bloqueada por inadimplência."
        tenant.blocked_at = datetime.now(timezone.utc)
        db.session.commit()
        return tenant

    def cancel(self, tenant: Tenant, reason: str | None = None) -> Tenant:
        tenant.status = TenantStatus.CANCELED
        tenant.blocked_reason = reason or "Conta cancelada."
        tenant.blocked_at = datetime.now(timezone.utc)
        db.session.commit()
        return tenant
