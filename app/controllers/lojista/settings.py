from datetime import datetime, timedelta, timezone

from flask import flash, make_response, redirect, render_template, request, url_for, Response

from app.forms.tenant_settings_forms import (
    AppearanceSettingsForm, CheckoutSettingsForm, MenuSettingsForm, OpeningHoursForm, StoreInfoForm,
)
from app.models import InvoiceStatus, TenantStatus
from app.repositories.plan_repository import PlanRepository
from app.services.admin_billing_service import AdminBillingService
from app.services.tenant_settings_service import TenantSettingsError, TenantSettingsService
from app.utils.decorators import lojista_login_required, lojista_required
from app.utils.tenant_context import get_current_tenant
from app.utils.uploads import InvalidImageError

from app.controllers.lojista import lojista_bp

# Janela em que o banner "Pagamento confirmado!" continua aparecendo no
# topo do painel de Faturas (ver _invoices_panel.html) depois de uma
# fatura ser marcada como paga — passado esse tempo, some sozinho (a
# confirmação já ficou registrada pra sempre no chat de suporte, ver
# AdminBillingService._send_payment_confirmed_message).
RECENTLY_PAID_WINDOW_MINUTES = 5


def _billing_panel_context(tenant, service: AdminBillingService) -> dict:
    """Contexto compartilhado por settings_subscription e seu fragmento
    de polling (settings_subscription_billing_fragment) — mantém as
    duas rotas sempre calculando `awaiting_payment`/`recently_paid_invoice`
    do mesmo jeito, ver lojista/settings/_invoices_panel.html."""
    invoices = service.list_invoices_for_tenant(tenant.id)

    awaiting_payment = any(
        inv.payment_link_url and inv.status in (InvoiceStatus.PENDING, InvoiceStatus.OVERDUE) for inv in invoices
    )

    recently_paid_invoice = None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RECENTLY_PAID_WINDOW_MINUTES)
    paid_invoices = [inv for inv in invoices if inv.status == InvoiceStatus.PAID and inv.paid_at]
    if paid_invoices:
        most_recent = max(paid_invoices, key=lambda inv: inv.paid_at)
        paid_at = most_recent.paid_at if most_recent.paid_at.tzinfo else most_recent.paid_at.replace(tzinfo=timezone.utc)
        if paid_at >= cutoff:
            recently_paid_invoice = most_recent

    return {"invoices": invoices, "awaiting_payment": awaiting_payment, "recently_paid_invoice": recently_paid_invoice}


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
                menu_tagline=form.menu_tagline.data, legal_name=form.legal_name.data, document=form.document.data,
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
            panel_theme=form.panel_theme.data,
            public_theme=form.public_theme.data,
        )
        flash("Aparência atualizada.", "success")
        return redirect(url_for("lojista.settings_appearance"))

    if not form.is_submitted():
        form.accent_color.data = (tenant.theme_settings or {}).get("accent") or "#E8A33D"
        form.theme_mode.data = (tenant.theme_settings or {}).get("mode") or "dark"
        form.panel_theme.data = tenant.panel_theme or ""
        form.public_theme.data = tenant.public_theme or ""

    from app.models.platform_settings import SYSTEM_THEME_ACCENTS, SYSTEM_THEME_SWATCHES

    return render_template(
        "lojista/settings/appearance.html", form=form, tenant=tenant,
        system_theme_swatches=SYSTEM_THEME_SWATCHES, system_theme_accents=SYSTEM_THEME_ACCENTS,
    )


@lojista_bp.route("/configuracoes/assinatura")
@lojista_required
def settings_subscription():
    tenant = get_current_tenant()
    service = AdminBillingService()
    subscription = service.get_subscription_for_tenant(tenant.id)
    available_plans = PlanRepository().list_active_for_landing()
    return render_template(
        "lojista/settings/subscription.html", tenant=tenant, subscription=subscription,
        available_plans=available_plans, **_billing_panel_context(tenant, service),
    )


@lojista_bp.route("/configuracoes/assinatura/fragmento")
@lojista_required
def settings_subscription_billing_fragment():
    """Fragmento reaproveitado pelo polling HTMX do painel de Faturas
    (ver lojista/settings/_invoices_panel.html, hx-trigger="every 8s")
    enquanto houver uma fatura aguardando pagamento via Pix — é o que dá
    a sensação de "tempo real" quando o webhook do Asaas confirma o
    pagamento (ver AdminBillingService.mark_paid) sem o lojista precisar
    atualizar a página manualmente."""
    tenant = get_current_tenant()
    service = AdminBillingService()
    return render_template("lojista/settings/_invoices_panel.html", **_billing_panel_context(tenant, service))


@lojista_bp.route("/configuracoes/assinatura/faturas/<int:invoice_id>")
@lojista_required
def settings_subscription_invoice_detail(invoice_id):
    """Detalhe de UMA fatura — pra o lojista entender de onde veio
    aquele valor (renovação normal, prorateio de upgrade, cobrança
    manual do Super Admin etc.) sem precisar perguntar ao suporte. Só
    mostra fatura da própria loja (confere `tenant_id` explicitamente,
    mesmo a query já vindo de dentro do painel autenticado — segunda
    camada de isolamento entre lojistas, ver base_repository.py)."""
    tenant = get_current_tenant()
    service = AdminBillingService()
    invoice = service.get_invoice_or_404(invoice_id)
    if invoice.tenant_id != tenant.id:
        from flask import abort

        abort(404)

    return render_template("lojista/settings/invoice_detail.html", tenant=tenant, invoice=invoice)


@lojista_bp.route("/configuracoes/assinatura/plano", methods=["POST"])
@lojista_required
def settings_subscription_change_plan():
    """O lojista escolhe/troca de plano — só aplicado após ele confirmar
    no modal de confirmação do formulário (ver
    lojista/settings/subscription.html, data-confirm-message), nunca
    silenciosamente."""
    tenant = get_current_tenant()
    plan_id = request.form.get("plan_id", type=int)

    if not plan_id:
        flash("Selecione um plano válido.", "danger")
        return redirect(url_for("lojista.settings_subscription"))

    try:
        result = TenantSettingsService(tenant).change_plan(plan_id)
    except TenantSettingsError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("lojista.settings_subscription"))

    plan = result.plan
    if result.kind == "trial_confirmed":
        flash(
            f"Plano '{plan.name}' confirmado! Como você ainda está no período de teste, nenhuma cobrança "
            "é feita agora — a primeira fatura só é gerada no dia seguinte ao fim do seu teste grátis, "
            "sem perder os dias que ainda restam.",
            "success",
        )
    elif result.kind == "upgrade":
        if result.proration_cents > 0:
            flash(
                f"Plano alterado para '{plan.name}' e já está valendo! Foi gerada uma fatura de R$ "
                f"{result.proration_cents / 100:.2f} referente à diferença proporcional aos dias restantes "
                "deste ciclo — a partir da próxima renovação, o valor cobrado já é o cheio do novo plano. "
                "Você confere a fatura logo abaixo antes de pagar.",
                "success",
            )
        else:
            flash(f"Plano alterado para '{plan.name}' e já está valendo, sem nenhuma cobrança adicional agora.", "success")
    elif result.kind == "downgrade_scheduled":
        effective = result.effective_date.strftime("%d/%m/%Y") if result.effective_date else "no fim do ciclo atual"
        flash(
            f"Pedido de troca para o plano '{plan.name}' recebido. Como é um plano mais barato, a troca só "
            f"entra em vigor em {effective} — você continua no plano '{result.current_plan.name}' até lá, "
            "sem nenhum estorno, já que esse período já está pago. Nenhuma cobrança é feita agora.",
            "success",
        )
    elif result.kind == "reaffirmed_canceled_downgrade":
        flash(
            f"Você continua no plano '{plan.name}'. A troca que estava agendada para um plano diferente foi "
            "cancelada.",
            "success",
        )
    elif result.kind == "reaffirmed":
        flash(f"Você já está no plano '{plan.name}' — nada mudou.", "success")
    else:
        flash(
            f"Plano associado à sua loja alterado para '{plan.name}'. Nenhuma cobrança foi "
            "gerada agora — a fatura, quando houver, aparece aqui antes de qualquer pagamento.",
            "success",
        )

    return redirect(url_for("lojista.settings_subscription"))
