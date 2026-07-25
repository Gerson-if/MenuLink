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


def not_blank(form, field):
    """Rejeita um texto que só tem espaços em branco (DataRequired sozinho
    deixa passar ' ' já que não está tecnicamente vazio)."""
    if field.data and not field.data.strip():
        raise ValidationError("Este campo não pode ficar em branco.")


_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def slug_format(form, field):
    if field.data and not _SLUG_RE.fullmatch(field.data):
        raise ValidationError("Use apenas letras minúsculas, números e hífens.")
