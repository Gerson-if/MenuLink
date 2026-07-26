from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from app.forms.admin_forms import AdminAppearanceForm, AsaasSettingsForm, EmailSettingsForm, EmailTestForm, LandingContentForm, LegalContentForm, TrialSettingsForm
from app.services.email_service import EmailError
from app.services.payment_gateway.base import PaymentGatewayError
from app.services.platform_settings_service import PlatformSettingsService
from app.utils.decorators import super_admin_required
from app.utils.uploads import InvalidImageError, InvalidVideoError

from app.controllers.admin import admin_bp


@admin_bp.route("/configuracoes/asaas", methods=["GET", "POST"])
@super_admin_required
def settings_asaas():
    service = PlatformSettingsService()
    settings = service.settings
    form = AsaasSettingsForm()

    if not form.is_submitted():
        form.environment.data = settings.asaas_environment

    if form.validate_on_submit():
        service.update_asaas(
            environment=form.environment.data,
            api_key=form.api_key.data,
            clear_api_key=form.clear_api_key.data,
            webhook_token=form.webhook_token.data,
            clear_webhook_token=form.clear_webhook_token.data,
        )
        flash("Configurações do Asaas atualizadas.", "success")

        if settings.asaas_configured and not settings.asaas_webhook_configured:
            flash(
                "A chave de API está configurada, mas o token de webhook não. "
                "Sem o webhook, os pagamentos gerados no Asaas não liberam o "
                "lojista automaticamente — alguém precisa marcar a fatura como "
                "paga manualmente. Gere um token abaixo e cadastre a URL de "
                "webhook no painel do Asaas.",
                "warning",
            )
        return redirect(url_for("admin.settings_asaas"))

    webhook_url = url_for("webhooks.asaas_webhook", _external=True)

    return render_template(
        "admin/settings/asaas.html", form=form, settings=settings, webhook_url=webhook_url,
    )


@admin_bp.route("/configuracoes/asaas/gerar-token", methods=["POST"])
@super_admin_required
def settings_asaas_generate_token():
    service = PlatformSettingsService()
    token = service.generate_webhook_token()
    flash(
        f"Novo token de webhook gerado: {token} — copie agora e cadastre no "
        "painel do Asaas. Ele não será mostrado novamente nesta tela (mas você "
        "pode gerar outro a qualquer momento, sem perder a integração).",
        "success",
    )
    return redirect(url_for("admin.settings_asaas"))


@admin_bp.route("/configuracoes/asaas/testar", methods=["POST"])
@super_admin_required
def settings_asaas_test():
    service = PlatformSettingsService()
    try:
        service.test_connection()
    except PaymentGatewayError as exc:
        flash(f"Falha ao conectar com o Asaas: {exc}", "danger")
    else:
        flash("Conexão com o Asaas funcionando — a chave de API é válida.", "success")
    return redirect(url_for("admin.settings_asaas"))


@admin_bp.route("/configuracoes/email", methods=["GET", "POST"])
@super_admin_required
def settings_email():
    service = PlatformSettingsService()
    settings = service.settings
    form = EmailSettingsForm()
    test_form = EmailTestForm()

    if not form.is_submitted():
        form.host.data = settings.smtp_host or ""
        form.port.data = settings.smtp_port or 587
        form.encryption.data = settings.smtp_encryption or "tls"
        form.username.data = settings.smtp_username or ""
        form.from_email.data = settings.smtp_from_email or ""
        form.from_name.data = settings.smtp_from_name or ""

    if form.validate_on_submit():
        service.update_email(
            host=form.host.data, port=form.port.data, username=form.username.data,
            password=form.password.data, clear_password=form.clear_password.data,
            encryption=form.encryption.data, from_email=form.from_email.data, from_name=form.from_name.data,
        )
        flash("Configurações de e-mail atualizadas.", "success")
        if not settings.smtp_password and not form.clear_password.data:
            flash(
                "Servidor e remetente salvos, mas nenhuma senha foi definida ainda — a maioria dos "
                "provedores SMTP exige autenticação. Envie um e-mail de teste abaixo para confirmar.",
                "warning",
            )
        return redirect(url_for("admin.settings_email"))

    if not test_form.is_submitted():
        test_form.to.data = current_user.email

    return render_template("admin/settings/email.html", form=form, test_form=test_form, settings=settings)


@admin_bp.route("/configuracoes/email/testar", methods=["POST"])
@super_admin_required
def settings_email_test():
    test_form = EmailTestForm()
    if not test_form.validate_on_submit():
        flash("Informe um e-mail válido para o teste.", "danger")
        return redirect(url_for("admin.settings_email"))

    service = PlatformSettingsService()
    try:
        service.send_test_email(to=test_form.to.data)
    except EmailError as exc:
        flash(f"Falha ao enviar o e-mail de teste: {exc}", "danger")
    else:
        flash(f"E-mail de teste enviado para {test_form.to.data} — confira a caixa de entrada (e o spam).", "success")
    return redirect(url_for("admin.settings_email"))


@admin_bp.route("/configuracoes/legal", methods=["GET", "POST"])
@super_admin_required
def settings_legal():
    service = PlatformSettingsService()
    settings = service.settings
    form = LegalContentForm()

    if not form.is_submitted():
        form.terms_content.data = settings.terms_content or ""
        form.privacy_content.data = settings.privacy_content or ""

    if form.validate_on_submit():
        service.update_legal_content(
            terms_content=form.terms_content.data, privacy_content=form.privacy_content.data,
        )
        flash("Termos de Uso e Política de Privacidade atualizados.", "success")
        return redirect(url_for("admin.settings_legal"))

    return render_template(
        "admin/settings/legal.html", form=form, settings=settings,
        terms_url=url_for("terms_of_use", _external=True), privacy_url=url_for("privacy_policy", _external=True),
    )


@admin_bp.route("/configuracoes/teste-gratis", methods=["GET", "POST"])
@super_admin_required
def settings_trial():
    service = PlatformSettingsService()
    settings = service.settings
    form = TrialSettingsForm()

    if not form.is_submitted():
        form.trial_days.data = settings.trial_days
        form.trial_grace_days.data = settings.trial_grace_days
        form.trial_reminder_days_before.data = settings.trial_reminder_days_before
        form.trial_reminder_cycle_days.data = settings.trial_reminder_cycle_days
        form.trial_reminder_message.data = settings.trial_reminder_message or ""

    if form.validate_on_submit():
        service.update_trial_settings(
            trial_days=form.trial_days.data,
            trial_grace_days=form.trial_grace_days.data,
            trial_reminder_days_before=form.trial_reminder_days_before.data,
            trial_reminder_cycle_days=form.trial_reminder_cycle_days.data,
            trial_reminder_message=form.trial_reminder_message.data,
        )
        flash("Configurações do período de teste atualizadas.", "success")
        return redirect(url_for("admin.settings_trial"))

    return render_template("admin/settings/trial.html", form=form, settings=settings)


@admin_bp.route("/configuracoes/aparencia", methods=["GET", "POST"])
@super_admin_required
def settings_appearance():
    service = PlatformSettingsService()
    settings = service.settings
    form = AdminAppearanceForm()

    if not form.is_submitted():
        form.accent_color.data = settings.admin_theme_accent or "#E54A36"
        form.support_whatsapp.data = settings.support_whatsapp or ""

    if form.validate_on_submit():
        try:
            service.update_admin_appearance(
                accent_color=form.accent_color.data, reset_to_default=form.reset_to_default.data,
                platform_logo_file=form.platform_logo.data, support_whatsapp=form.support_whatsapp.data,
            )
        except InvalidImageError as exc:
            flash(str(exc), "danger")
        else:
            flash("Aparência do painel atualizada.", "success")
            return redirect(url_for("admin.settings_appearance"))

    return render_template("admin/settings/appearance.html", form=form, settings=settings)


@admin_bp.route("/configuracoes/aparencia/logo/remover", methods=["POST"])
@super_admin_required
def settings_appearance_remove_logo():
    """Ação isolada do ícone de remover no dropzone da logo do sistema —
    efeito imediato, não depende do envio do resto do form de Aparência
    (mesmo padrão de settings_landing_remove_media)."""
    if not request.headers.get("HX-Request"):
        return "", 400

    service = PlatformSettingsService()
    service.remove_platform_logo()
    settings = service.settings

    form = AdminAppearanceForm()
    html = render_template("admin/settings/_logo_dropzone_fragment.html", form=form, settings=settings)
    return make_response(html)


@admin_bp.route("/configuracoes/landing", methods=["GET", "POST"])
@super_admin_required
def settings_landing():
    service = PlatformSettingsService()
    settings = service.settings
    form = LandingContentForm()

    if not form.is_submitted():
        content = settings.landing_content_or_default
        form.hero_title.data = content["hero_title"]
        form.hero_subtitle.data = content["hero_subtitle"]
        form.hero_image_opacity.data = content.get("hero_image_opacity", 100)
        form.hero_image_blur.data = content.get("hero_image_blur", 0)
        form.theme.data = content.get("theme", "chili")
        features = content["features"]
        for i in range(3):
            feature = features[i] if i < len(features) else {}
            getattr(form, f"feature{i + 1}_icon").data = feature.get("icon", "")
            getattr(form, f"feature{i + 1}_title").data = feature.get("title", "")
            getattr(form, f"feature{i + 1}_description").data = feature.get("description", "")

    if form.validate_on_submit():
        try:
            service.update_landing_content(
                hero_title=form.hero_title.data,
                hero_subtitle=form.hero_subtitle.data,
                hero_image_file=form.hero_image.data,
                hero_image_opacity=form.hero_image_opacity.data,
                hero_image_blur=form.hero_image_blur.data,
                hero_video_file=form.hero_video.data,
                theme=form.theme.data,
                features=[
                    {
                        "icon": getattr(form, f"feature{i + 1}_icon").data,
                        "title": getattr(form, f"feature{i + 1}_title").data,
                        "description": getattr(form, f"feature{i + 1}_description").data,
                    }
                    for i in range(3)
                ],
                feature_image_files=[getattr(form, f"feature{i + 1}_image").data for i in range(3)],
            )
        except (InvalidImageError, InvalidVideoError) as exc:
            flash(str(exc), "danger")
        else:
            flash("Landing page atualizada.", "success")
            if getattr(settings, "video_compression_warning", None):
                flash(settings.video_compression_warning, "warning")
            return redirect(url_for("admin.settings_landing"))

    return render_template("admin/settings/landing.html", form=form, settings=settings)


@admin_bp.route("/configuracoes/landing/midia/<field>/remover", methods=["POST"])
@super_admin_required
def settings_landing_remove_media(field):
    """Ação isolada do ícone de remover no dropzone — apaga o arquivo e
    limpa o campo na hora, sem depender do envio do resto do formulário
    da landing page (ver LandingContentForm/settings_landing)."""
    if not request.headers.get("HX-Request"):
        return "", 400
    if field not in PlatformSettingsService.LANDING_MEDIA_FIELDS:
        abort(404)

    service = PlatformSettingsService()
    service.remove_landing_media(field)
    content = service.settings.landing_content_or_default

    form = LandingContentForm()
    html = render_template("admin/settings/_media_dropzone_fragment.html", field=field, form=form, content=content)
    return make_response(html)
