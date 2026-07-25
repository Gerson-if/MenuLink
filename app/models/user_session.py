"""
Sessão de login rastreada (UserSession) — permite ao próprio usuário
(lojista ou Super Admin) ver de onde está logado e encerrar sessões
específicas (ou todas as outras) se desconfiar de algo estranho, sem
precisar trocar a senha para isso.

Independente do cookie de sessão do Flask-Login: aqui só guardamos um
token opaco (gerado no login, salvo também no cookie de sessão) e os
metadados de "onde/quando" — nunca a sessão do Flask-Login em si.
Revogar (`revoked_at`) não invalida o cookie por conta própria; o
próximo request do dono do cookie é que é barrado, ver
app/utils/session_guard.py (checado em todo request autenticado).
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin, utcnow


class UserSession(db.Model, TimestampMixin):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    session_token = Column(String(64), nullable=False, unique=True, index=True)
    ip_address = Column(String(45), nullable=True)  # cabe IPv6
    user_agent = Column(String(255), nullable=True)

    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", backref="sessions")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self):
        return f"<UserSession user_id={self.user_id} revoked={self.is_revoked}>"
