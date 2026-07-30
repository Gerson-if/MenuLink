from flask import flash, redirect, render_template, url_for
from flask_login import current_user

from app.forms.feedback_forms import LojistaFeedbackForm
from app.services.feedback_service import FeedbackError, FeedbackService
from app.utils.decorators import lojista_login_required
from app.utils.tenant_context import get_current_tenant

from app.controllers.lojista import lojista_bp

# lojista_login_required (não lojista_required) DE PROPÓSITO, mesmo
# critério do chat de suporte (ver lojista/chat.py): uma loja bloqueada
# por inadimplência ainda pode querer reportar um problema ou sugerir
# algo, então não bloqueamos esta tela junto com o resto do painel.


@lojista_bp.route("/sugestoes", methods=["GET", "POST"])
@lojista_login_required
def feedback():
    tenant = get_current_tenant()
    form = LojistaFeedbackForm()
    service = FeedbackService()

    if form.validate_on_submit():
        try:
            service.submit(
                kind=form.kind.data,
                message=form.message.data,
                tenant_id=tenant.id if tenant else None,
                submitted_by_user=current_user,
            )
        except FeedbackError as exc:
            flash(str(exc), "danger")
        else:
            flash("Recebemos sua mensagem, obrigado! Vamos dar uma olhada.", "success")
            return redirect(url_for("lojista.feedback"))

    previous = service.repo.list_for_tenant(tenant.id) if tenant else []
    return render_template("lojista/feedback.html", form=form, previous_feedback=previous)
