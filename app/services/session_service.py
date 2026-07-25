"""
Rastreamento de sessões de login — usado pela seção "Segurança" de
qualquer usuário (Super Admin ou lojista): listar de onde está logado e
encerrar sessões específicas (ou todas as outras) sem precisar trocar a
senha. Ver app/models/user_session.py e app/utils/session_guard.py (que
aplica a revogação a cada request).
"""

import secrets
from datetime import datetime, timedelta, timezone

from flask import request
from flask import session as flask_session

from app.extensions import db
from app.models import UserSession

# Não escreve no banco a cada request só pra atualizar "visto por
# último" — isso transformaria toda navegação em uma escrita extra.
# Só atualiza se já fez mais de TOUCH_INTERVAL desde a última vez.
TOUCH_INTERVAL = timedelta(minutes=5)


class SessionService:
    def start_session(self, user) -> UserSession:
        """Chamado no login (form e auto-login do cadastro) — gera um
        token opaco novo, salva no cookie de sessão do Flask e cria o
        registro rastreável correspondente."""
        token = secrets.token_urlsafe(32)
        record = UserSession(
            user_id=user.id,
            session_token=token,
            ip_address=(request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()[:45],
            user_agent=(request.user_agent.string or "")[:255],
            last_seen_at=datetime.now(timezone.utc),
        )
        db.session.add(record)
        db.session.commit()
        flask_session["session_token"] = token
        return record

    def get_current_session(self) -> UserSession | None:
        token = flask_session.get("session_token")
        if not token:
            return None
        return UserSession.query.filter_by(session_token=token).first()

    def current_session_is_revoked(self) -> bool:
        record = self.get_current_session()
        return record is not None and record.is_revoked

    def touch_current_session(self) -> None:
        record = self.get_current_session()
        if record is None or record.is_revoked:
            return
        now = datetime.now(timezone.utc)
        last_seen = record.last_seen_at
        # SQLite (dev/testes) devolve datetime "naive" mesmo em colunas
        # DateTime(timezone=True) — ao contrário do Postgres (produção),
        # que já devolve com tzinfo. Sempre gravamos em UTC (ver
        # `default=utcnow` do model), então um valor naive aqui É UTC.
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen is None or (now - last_seen) > TOUCH_INTERVAL:
            record.last_seen_at = now
            db.session.commit()

    def revoke_current_session(self) -> None:
        """Usado no logout — mantém o registro consistente com o
        cookie que está de fato saindo (evita "sessão ativa" fantasma
        na lista depois de um logout normal)."""
        record = self.get_current_session()
        if record is not None and not record.is_revoked:
            record.revoked_at = datetime.now(timezone.utc)
            db.session.commit()

    def list_active_sessions(self, user_id: int) -> list[UserSession]:
        return (
            UserSession.query.filter_by(user_id=user_id, revoked_at=None)
            .order_by(UserSession.last_seen_at.desc())
            .all()
        )

    def revoke_session(self, session_id: int, *, user_id: int) -> UserSession | None:
        """`user_id` trava a revogação ao dono da sessão — um usuário só
        pode encerrar as próprias sessões, nunca as de outra pessoa."""
        record = UserSession.query.filter_by(id=session_id, user_id=user_id).first()
        if record is None:
            return None
        if not record.is_revoked:
            record.revoked_at = datetime.now(timezone.utc)
            db.session.commit()
        return record

    def revoke_all_other_sessions(self, user_id: int) -> int:
        """"Sair de todos os outros dispositivos" — mantém a sessão
        atual (a que está fazendo a própria requisição) ativa."""
        current_token = flask_session.get("session_token")
        query = UserSession.query.filter_by(user_id=user_id, revoked_at=None)
        if current_token:
            query = query.filter(UserSession.session_token != current_token)
        records = query.all()
        now = datetime.now(timezone.utc)
        for record in records:
            record.revoked_at = now
        db.session.commit()
        return len(records)
