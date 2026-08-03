"""
Criptografia simétrica em repouso para segredos de terceiro salvos na
tabela `platform_settings` (chave da API do Asaas, token de webhook do
Asaas, senha do SMTP) — ver `EncryptedString` em app/models/base.py.

Sem isso, qualquer acesso ao banco (backup vazado, réplica comprometida,
uma SQL injection não relacionada, um dump/export descuidado) expunha
essas credenciais em texto puro — o suficiente para forjar cobranças no
Asaas ou mandar e-mail como a plataforma. A senha do painel (User) já
era protegida (bcrypt, ver app/models/user.py); isso fecha a lacuna
equivalente para os segredos de integração.

A chave de criptografia é DERIVADA de SECRET_KEY (não é um segredo
novo pra gerenciar/rotacionar à parte) via SHA-256 — o mesmo padrão já
usado por app/utils/tokens.py (tokens assinados com SECRET_KEY). Isso
significa que girar SECRET_KEY também invalida os segredos já
criptografados; é uma troca aceitável aqui (perder a chave do Asaas
salva é recuperável reconfigurando-a no painel, bem menos grave que
girar SECRET_KEY sem invalidar sessões/tokens de redefinição de senha).
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, has_app_context


def _fernet() -> Fernet:
    secret_key = current_app.config["SECRET_KEY"]
    # Fernet exige uma chave de 32 bytes, urlsafe-base64. Derivamos de
    # SECRET_KEY (string arbitrária) com SHA-256, que sempre produz 32
    # bytes — não reaproveita SECRET_KEY diretamente como chave Fernet.
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str | None) -> str | None:
    """None/"" continuam None/"" (campo opcional, sem integração
    configurada) — só criptografa quando há algo de fato."""
    if not plaintext:
        return plaintext
    if not has_app_context():
        # Fora de um app context (ex.: script solto) não dá pra saber a
        # SECRET_KEY certa — falha alto e claro em vez de silenciosamente
        # gravar em texto puro.
        raise RuntimeError("encrypt_secret precisa rodar dentro de um contexto de aplicação Flask.")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str | None) -> str | None:
    """Descriptografa; se `ciphertext` não for um token Fernet válido
    (valor plaintext gravado ANTES desta proteção existir, ou vindo de
    algum outro lugar), devolve o valor como veio — mantém
    compatibilidade com dados antigos sem exigir uma migração de dados
    à parte. Da próxima vez que o valor for salvo (ex.: reconfigurar a
    chave do Asaas no painel), ele passa a ser gravado já criptografado."""
    if not ciphertext:
        return ciphertext
    if not has_app_context():
        return ciphertext
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ciphertext
