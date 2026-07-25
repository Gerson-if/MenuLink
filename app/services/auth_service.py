"""
Regras de negócio de autenticação.

Centraliza aqui (e não no controller) as regras de:
- credenciais inválidas
- usuário inativo
- conta do lojista (tenant) suspensa / bloqueada por inadimplência / cancelada

para que a mesma regra sirva tanto o login via formulário HTML (fase atual)
quanto uma futura API JSON, sem duplicar lógica.

BLOCKED_PAYMENT é um caso especial: ao contrário de SUSPENDED/CANCELED
(que continuam recusando o login aqui), esse status TEM login permitido
— o lojista cai numa página restrita só com as pendências dele (ver
`lojista_required`/`lojista_login_required` em app/utils/decorators.py e
`lojista.billing_lockout`), pra conseguir ver e pagar o que deve sem
precisar acionar o suporte.
"""

from datetime import datetime, timezone

from app.extensions import db
from app.models import User, TenantStatus
from app.repositories.user_repository import UserRepository


class AuthError(Exception):
    """Erro genérico de autenticação, com mensagem segura para exibir ao usuário."""


class InvalidCredentialsError(AuthError):
    def __init__(self):
        super().__init__("E-mail ou senha inválidos.")


class AccountInactiveError(AuthError):
    def __init__(self):
        super().__init__("Este usuário está inativo. Fale com o administrador.")


class TenantBlockedError(AuthError):
    def __init__(self, tenant_status: TenantStatus):
        messages = {
            TenantStatus.SUSPENDED: "Sua conta está suspensa. Fale com o suporte.",
            TenantStatus.BLOCKED_PAYMENT: "Sua conta está bloqueada por pendência de pagamento.",
            TenantStatus.CANCELED: "Sua conta foi cancelada.",
        }
        super().__init__(messages.get(tenant_status, "Sua conta não está disponível no momento."))


class AuthService:
    def __init__(self):
        self.user_repository = UserRepository()

    def authenticate(self, email: str, password: str) -> User:
        user = self.user_repository.get_by_email(email)

        if user is None or not user.check_password(password):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise AccountInactiveError()

        if not user.is_super_admin:
            tenant = user.tenant
            allowed_statuses = (TenantStatus.TRIAL, TenantStatus.ACTIVE, TenantStatus.BLOCKED_PAYMENT)
            if tenant is None or tenant.status not in allowed_statuses:
                status = tenant.status if tenant else TenantStatus.CANCELED
                raise TenantBlockedError(status)

        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()

        return user

    def change_password(self, user, *, current_password: str, new_password: str) -> None:
        """Troca de senha pelo próprio usuário logado (Super Admin ou
        lojista) — ao contrário do fluxo de "esqueci minha senha", exige
        confirmar a senha atual (evita que alguém com uma sessão aberta
        sem supervisão troque a senha de outra pessoa).

        Encerra automaticamente todas as OUTRAS sessões ativas dessa
        conta — prática padrão de segurança: se a troca foi motivada por
        suspeita de acesso indevido, um acesso que já estava aberto em
        outro lugar (antes de a senha nova existir) é derrubado no
        próximo request dele, sem exigir um passo manual extra."""
        if not user.check_password(current_password):
            raise AuthError("Senha atual incorreta.")
        user.set_password(new_password)
        db.session.commit()

        from app.services.session_service import SessionService

        SessionService().revoke_all_other_sessions(user.id)

    def update_own_profile(self, user, *, name: str, email: str) -> None:
        """Nome e e-mail de login do próprio usuário — ao contrário do
        cadastro de um novo lojista (feito pelo Super Admin), aqui não há
        confirmação por e-mail antes de trocar (mesma confiança que já
        existia antes: quem está com a sessão aberta e a senha atual é
        considerado o dono da conta)."""
        email = (email or "").strip().lower()
        existing = self.user_repository.get_by_email(email)
        if existing is not None and existing.id != user.id:
            raise AuthError("Este e-mail já está em uso por outra conta.")

        user.name = (name or "").strip()
        user.email = email
        db.session.commit()
