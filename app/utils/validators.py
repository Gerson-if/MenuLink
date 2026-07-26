"""
Validadores WTForms customizados e reutilizados em vários formulários.
"""

import re

from wtforms.validators import StopValidation, ValidationError

_PHONE_DIGITS_RE = re.compile(r"\D")


def bounded_length(max_chars: int, message: str | None = None):
    """Corta a validação do campo ANTES de validators mais caros — em
    especial `Email()`, que usa a lib `email_validator` e fica visivelmente
    lento (perto de O(n²)) em strings de centenas de milhares de
    caracteres: um campo de e-mail sem limite antes do Email() deixa
    /login, /cadastrar e /recuperar-senha (todos públicos, sem login)
    abertos a alguém travar um worker por vários segundos só colando um
    texto enorme no campo.

    Use como o PRIMEIRO validator da lista, antes de `Email()` — ao
    contrário de `wtforms.validators.Length` (que só registra o erro e
    deixa os validators seguintes rodarem do mesmo jeito), este levanta
    `StopValidation` e interrompe a cadeia ali mesmo."""

    def _validator(form, field):
        value = field.data or ""
        if len(value) > max_chars:
            raise StopValidation(message or f"Texto muito longo (máximo {max_chars} caracteres).")

    return _validator


def phone_number(min_digits: int = 10, max_digits: int = 15):
    """
    Valida um número de telefone/WhatsApp de forma tolerante a formatação
    (aceita espaços, parênteses, hífens, +) mas garante que, ao remover
    tudo que não é dígito, sobre uma quantidade razoável de números —
    evita cadastrar "abc" ou "11" como telefone.
    """

    def _validator(form, field):
        if not field.data:
            return  # campo opcional é responsabilidade de Optional()
        digits = _PHONE_DIGITS_RE.sub("", field.data)
        if not (min_digits <= len(digits) <= max_digits):
            raise ValidationError(
                f"Telefone inválido. Use um número com DDD (e DDI, se WhatsApp), "
                f"entre {min_digits} e {max_digits} dígitos."
            )

    return _validator


def password_max_bytes(max_bytes: int = 72, message: str | None = None):
    """bcrypt (usado em app/models/user.py via flask-bcrypt) só aceita até
    72 BYTES de senha — acima disso, `bcrypt.hashpw`/`check_password_hash`
    levanta `ValueError` e derruba a requisição com 500 (foi exatamente o
    que aconteceu em produção: alguém digitou uma senha grande, e mesmo com
    ~128 CARACTERES — dentro do limite de `Length(max=128)` — em UTF-8 isso
    pode passar de 72 BYTES quando há acentos/emoji, já que cada um ocupa
    até 4 bytes.

    Por isso não dá pra confiar só em `Length(max=...)`, que conta
    caracteres, não bytes. Este validador mede o tamanho já convertido pra
    UTF-8, do jeito que o bcrypt vai receber, e deve vir ANTES de
    `Length`/`DataRequired` na lista pra cortar a cadeia cedo com
    `StopValidation`."""

    def _validator(form, field):
        value = field.data or ""
        if len(value.encode("utf-8")) > max_bytes:
            raise StopValidation(
                message or f"A senha é muito longa (máximo {max_bytes} bytes; evite excesso de acentos/emojis)."
            )

    return _validator


def not_blank(form, field):
    """Rejeita um texto que só tem espaços em branco (DataRequired sozinho
    deixa passar ' ' já que não está tecnicamente vazio)."""
    if field.data and not field.data.strip():
        raise ValidationError("Este campo não pode ficar em branco.")


_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def slug_format(form, field):
    if field.data and not _SLUG_RE.fullmatch(field.data):
        raise ValidationError("Use apenas letras minúsculas, números e hífens.")


_NON_DIGITS_RE = re.compile(r"\D")


def only_digits(value: str) -> str:
    """Remove tudo que não é dígito — usado tanto na validação de
    CPF/CNPJ quanto na hora de montar o payload pra Asaas (que exige
    `cpfCnpj` só com números, sem pontos/traço/barra)."""
    return _NON_DIGITS_RE.sub("", value or "")


def _cpf_check_digit(digits: str) -> int:
    factor = len(digits) + 1
    total = sum(int(d) * f for d, f in zip(digits, range(factor, 1, -1)))
    rest = (total * 10) % 11
    return 0 if rest >= 10 else rest


def _is_valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    d1 = _cpf_check_digit(digits[:9])
    d2 = _cpf_check_digit(digits[:9] + str(d1))
    return digits[-2:] == f"{d1}{d2}"


def _cnpj_check_digit(digits: str, weights: list[int]) -> int:
    total = sum(int(d) * w for d, w in zip(digits, weights))
    rest = total % 11
    return 0 if rest < 2 else 11 - rest


def _is_valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _cnpj_check_digit(digits[:12], weights1)
    d2 = _cnpj_check_digit(digits[:12] + str(d1), weights2)
    return digits[-2:] == f"{d1}{d2}"


def cpf_cnpj(form, field):
    """Valida CPF (11 dígitos) ou CNPJ (14 dígitos), tolerante a
    formatação (pontos, traço, barra) — mesmo espírito de
    `phone_number()`. Campo opcional: string vazia não é validada aqui
    (responsabilidade de `Optional()`). Este documento é o que a
    integração com o Asaas usa como `cpfCnpj` ao criar o cliente de
    cobrança (ver AsaasGateway.ensure_customer) — sem um CPF/CNPJ
    válido cadastrado, a geração de cobrança real falha lá na frente,
    então validamos aqui, na hora do cadastro, pra pegar o erro cedo."""
    if not field.data:
        return
    digits = only_digits(field.data)
    if len(digits) == 11:
        if not _is_valid_cpf(digits):
            raise ValidationError("CPF inválido.")
    elif len(digits) == 14:
        if not _is_valid_cnpj(digits):
            raise ValidationError("CNPJ inválido.")
    else:
        raise ValidationError("Informe um CPF (11 dígitos) ou CNPJ (14 dígitos) válido.")
