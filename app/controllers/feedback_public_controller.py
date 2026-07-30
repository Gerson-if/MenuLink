"""
Formulário público de sugestões/reporte de erros, acessível a qualquer
visitante da landing page (sem precisar de conta) — ver
app/models/feedback.py para o desenho compartilhado com a versão do
painel do lojista (app/controllers/lojista/feedback.py).
"""

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user

from app.extensions import limiter
from app.forms.feedback_forms import PublicFeedbackForm
from app.services.feedback_service import FeedbackError, FeedbackService
from app.utils.tenant_context import get_current_tenant

feedback_bp = Blueprint("feedback", __name__)


@feedback_bp.route("/sugestoes", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def public_feedback():
    form = PublicFeedbackForm()
    if form.validate_on_submit():
        # Se por acaso um lojista já logado chegar aqui (ex: link
        # compartilhado), aproveita a conta/loja dele em vez de tratar
        # como visitante anônimo — mesmo dado que a versão logada do
        # formulário (painel do lojista) registraria.
        tenant = get_current_tenant() if current_user.is_authenticated else None
        try:
            FeedbackService().submit(
                kind=form.kind.data,
                message=form.message.data,
                tenant_id=tenant.id if tenant else None,
                submitted_by_user=current_user if current_user.is_authenticated else None,
                name=form.name.data,
                email=form.email.data,
            )
        except FeedbackError as exc:
            flash(str(exc), "danger")
        else:
            flash("Recebemos sua mensagem, obrigado! Vamos dar uma olhada.", "success")
            return redirect(url_for("feedback.public_feedback"))

    return render_template("marketing/feedback.html", form=form)
