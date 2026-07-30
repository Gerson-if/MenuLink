from flask import flash, redirect, render_template, request, url_for

from app.forms.feedback_forms import FeedbackStatusForm
from app.services.feedback_service import FeedbackError, FeedbackService
from app.utils.decorators import super_admin_required

from app.controllers.admin import admin_bp


@admin_bp.route("/sugestoes")
@super_admin_required
def feedback_list():
    status = request.args.get("status", "").strip()
    kind = request.args.get("kind", "").strip()
    page = request.args.get("page", 1, type=int)

    pagination = FeedbackService().list_for_admin(page=page, status=status, kind=kind)
    return render_template(
        "admin/feedback/index.html", pagination=pagination, items=pagination.items,
        status=status, kind=kind, status_form=FeedbackStatusForm(),
    )


@admin_bp.route("/sugestoes/<int:feedback_id>/status", methods=["POST"])
@super_admin_required
def feedback_update_status(feedback_id):
    service = FeedbackService()
    item = service.get_or_404(feedback_id)
    form = FeedbackStatusForm()
    if form.validate_on_submit():
        try:
            service.update_status(item, status=form.status.data, admin_notes=form.admin_notes.data)
        except FeedbackError as exc:
            flash(str(exc), "danger")
        else:
            flash("Atualizado.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")

    # Preserva o filtro/página que o Super Admin estava vendo — vêm na
    # própria query string da URL de ação do form (ver
    # admin/feedback/index.html), não no corpo do POST.
    return redirect(
        url_for(
            "admin.feedback_list",
            status=request.args.get("status", ""),
            kind=request.args.get("kind", ""),
            page=request.args.get("page", 1),
        )
    )
