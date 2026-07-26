from flask import flash, make_response, redirect, render_template, request, url_for, Response

from app.forms.tenant_settings_forms import (
    AppearanceSettingsForm, CheckoutSettingsForm, MenuSettingsForm, OpeningHoursForm, StoreInfoForm,
)
from app.models import InvoiceStatus, TenantStatus
from app.services.admin_billing_service import AdminBillingService
from app.services.tenant_settings_service import TenantSettingsError, TenantSettingsService
from app.utils.decorators import lojista_login_required, lojista_required
from app.utils.tenant_context import get_current_tenant
from app.utils.uploads import InvalidImageError

from app.controllers.lojista import lojista_bp


@lojista_bp.route("/conta-bloqueada")
@lojista_login_required
def billing_lockout():
    """Única página acessível por uma conta BLOCKED_PAYMENT (ver
    `lojista_required`, que redireciona pra cá) — mostra só as faturas em
    aberto, pra regularizar sem precisar do suporte. Uma conta que NÃO
    está bloqueada e cair aqui (ex: link salvo de quando esteve
    bloqueada) volta direto pro painel normal."""
    tenant = get_current_tenant()
    if tenant.status != TenantStatus.BLOCKED_PAYMENT:
        return redirect(url_for("lojista.dashboard"))

    invoices = AdminBillingService().list_invoices_for_tenant(tenant.id)
    open_invoices = [inv for inv in invoices if inv.status in (InvoiceStatus.PENDING, InvoiceStatus.OVERDUE)]
    return render_template(
        "lojista/billing_lockout.html", tenant=tenant, open_invoices=open_invoices,
    )


@lojista_bp.route("/configuracoes/loja", methods=["GET", "POST"])
@lojista_required
def settings_store():
    tenant = get_current_tenant()
    form = StoreInfoForm(obj=tenant)
    service = TenantSettingsService(tenant)

    if form.validate_on_submit():
        try:
            service.update_store_info(
                name=form.name.data, whatsapp_number=form.whatsapp_number.data,
                phone=form.phone.data, logo_file_storage=form.logo.data,
                address_street=form.address_street.data, address_number=form.address_number.data,
                address_neighborhood=form.address_neighborhood.data, address_city=form.address_city.data,
                menu_tagline=form.menu_tagline.data,
            )
        except InvalidImageError as exc:
            flash(str(exc), "danger")
        else:
            flash("Dados da loja atualizados.", "success")
            return redirect(url_for("lojista.settings_store"))

    return render_template("lojista/settings/store.html", form=form, tenant=tenant)


@lojista_bp.route("/configuracoes/loja/logo/remover", methods=["POST"])
@lojista_required
def settings_store_remove_logo():
    """Ação isolada do ícone de remover no dropzone — some com a logo
    na hora, sem depender do restante do formulário de dados da loja
    (ver StoreInfoForm/settings_store)."""
    if not request.headers.get("HX-Request"):
        return "", 400

    tenant = get_current_tenant()
    TenantSettingsService(tenant).remove_logo()

    form = StoreInfoForm(obj=tenant)
    html = render_template("lojista/settings/_logo_dropzone.html", form=form, tenant=tenant)
    # A marca no topo da sidebar (layouts/lojista_panel.html) também
    # mostra a logo e não faz parte do fragmento acima — atualiza via
    # out-of-band swap pra não ficar com a logo antiga até recarregar.
    html += (
        '<div id="sidebar-brand-mark" class="mark" hx-swap-oob="true">'
        '<i class="bi bi-egg-fried text-white"></i></div>'
    )
    return make_response(html)


@lojista_bp.route("/configuracoes/horario", methods=["GET", "POST"])
@lojista_required
def settings_hours():
    tenant = get_current_tenant()
    form = OpeningHoursForm()
    service = TenantSettingsService(tenant)

    if form.validate_on_submit():
        days = {key: subform.data for key, subform in form.days()}
        service.update_opening_hours(days)
        flash("Horário de funcionamento atualizado.", "success")
        return redirect(url_for("lojista.settings_hours"))

    if not form.is_submitted():
        saved = tenant.opening_hours or {}
        for key, subform in form.days():
            day = saved.get(key)
            # Dia nunca configurado: começa marcado como "fechado" (o
            # lojista precisa abrir explicitamente cada dia de operação).
            subform.closed.data = True if not day else bool(day.get("closed"))
            subform.open.data = (day or {}).get("open", "")
            subform.close.data = (day or {}).get("close", "")

    return render_template("lojista/settings/hours.html", form=form, tenant=tenant)


@lojista_bp.route("/configuracoes/cardapio", methods=["GET", "POST"])
@lojista_required
def settings_menu():
    tenant = get_current_tenant()
    form = MenuSettingsForm(obj=tenant)
    service = TenantSettingsService(tenant)

    if not form.is_submitted():
        form.notes_placeholder.data = (tenant.theme_settings or {}).get("notes_placeholder", "")

    if form.validate_on_submit():
        try:
            service.update_menu_settings(
                slug=form.slug.data, pickup_enabled=form.pickup_enabled.data,
                delivery_enabled=form.delivery_enabled.data,
                show_price_from_label=form.show_price_from_label.data,
                notes_placeholder=form.notes_placeholder.data,
            )
        except TenantSettingsError as exc:
            flash(str(exc), "danger")
        else:
            flash("Configurações do cardápio atualizadas.", "success")
            return redirect(url_for("lojista.settings_menu"))

    cardapio_url = url_for("public.store_home", slug=tenant.slug, _external=True)
    qr_code_url = url_for("lojista.settings_menu_qrcode")

    return render_template(
        "lojista/settings/menu.html", form=form, tenant=tenant,
        cardapio_url=cardapio_url, qr_code_url=qr_code_url,
    )


@lojista_bp.route("/configuracoes/cardapio/qrcode.png")
@lojista_required
def settings_menu_qrcode():
    """
    Gera o QR Code do cardápio localmente (biblioteca `qrcode`), sem
    depender de nenhuma API externa — evita que a funcionalidade quebre
    se um serviço de terceiros estiver fora do ar ou bloqueado pela
    rede onde a aplicação está hospedada.
    """
    import io

    import qrcode

    tenant = get_current_tenant()
    cardapio_url = url_for("public.store_home", slug=tenant.slug, _external=True)

    img = qrcode.make(cardapio_url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return Response(buf.getvalue(), mimetype="image/png")


@lojista_bp.route("/configuracoes/checkout", methods=["GET", "POST"])
@lojista_required
def settings_checkout():
    tenant = get_current_tenant()
    form = CheckoutSettingsForm()
    service = TenantSettingsService(tenant)

    if form.validate_on_submit():
        service.update_checkout_settings(
            delivery_fee_reais=float(form.delivery_fee.data) if form.delivery_fee.data is not None else None,
            free_delivery_above_reais=float(form.free_delivery_above.data) if form.free_delivery_above.data is not None else None,
            min_order_reais=float(form.min_order.data) if form.min_order.data is not None else None,
            accept_pix=form.accept_pix.data, accept_card=form.accept_card.data,
            accept_cash=form.accept_cash.data, accept_other=form.accept_other.data,
        )
        flash("Configurações de checkout atualizadas.", "success")
        return redirect(url_for("lojista.settings_checkout"))

    if not form.is_submitted():
        form.delivery_fee.data = (tenant.delivery_fee_cents / 100) if tenant.delivery_fee_cents else None
        form.free_delivery_above.data = (tenant.free_delivery_above_cents / 100) if tenant.free_delivery_above_cents else None
        form.min_order.data = (tenant.min_order_cents / 100) if tenant.min_order_cents else None
        form.accept_pix.data = tenant.accept_pix
        form.accept_card.data = tenant.accept_card
        form.accept_cash.data = tenant.accept_cash
        form.accept_other.data = tenant.accept_other

    return render_template("lojista/settings/checkout.html", form=form, tenant=tenant)


@lojista_bp.route("/configuracoes/aparencia", methods=["GET", "POST"])
@lojista_required
def settings_appearance():
    tenant = get_current_tenant()
    form = AppearanceSettingsForm()
    service = TenantSettingsService(tenant)

    if form.validate_on_submit():
        service.update_appearance(
            accent_color=form.accent_color.data,
            reset_to_default=form.reset_to_default.data,
            theme_mode=form.theme_mode.data,
        )
        flash("Aparência do cardápio atualizada.", "success")
        return redirect(url_for("lojista.settings_appearance"))

    if not form.is_submitted():
        form.accent_color.data = (tenant.theme_settings or {}).get("accent") or "#E8A33D"
        form.theme_mode.data = (tenant.theme_settings or {}).get("mode") or "dark"

    return render_template("lojista/settings/appearance.html", form=form, tenant=tenant)


@lojista_bp.route("/configuracoes/assinatura")
@lojista_required
def settings_subscription():
    tenant = get_current_tenant()
    service = AdminBillingService()
    invoices = service.list_invoices_for_tenant(tenant.id)
    subscription = service.get_subscription_for_tenant(tenant.id)
    return render_template(
        "lojista/settings/subscription.html", tenant=tenant, invoices=invoices, subscription=subscription,
    )
