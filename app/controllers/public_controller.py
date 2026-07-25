"""
Cardápio público — acessível sem login, por slug (/loja/<slug>).

- GET  /loja/<slug>          -> página do cardápio (categorias, produtos)
- POST /loja/<slug>/pedido   -> cria o pedido (JSON) e retorna o link do
                                 WhatsApp para o cliente finalizar o envio
"""

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, session
from flask_wtf.csrf import validate_csrf, CSRFError
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.controllers.lojista.orders import STATUS_LABELS
from app.extensions import csrf, db, limiter
from app.models import Category, ComplementGroup, Order, OrderItem, Product, Tenant, TenantStatus
from app.repositories.customer_repository import CustomerRepository
from app.schemas.checkout_schema import CheckoutSchema
from app.services.order_service import OrderService, OrderValidationError
from app.utils.phone import normalize_br_phone
from app.utils.tenant_context import resolve_tenant_by_slug
from app.utils.whatsapp import build_order_message, build_whatsapp_url

public_bp = Blueprint("public", __name__, url_prefix="/loja")


def _serialize_product(product):
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description or "",
        "price_cents": product.price_cents,
        "tag": product.tag,
        "has_complements": bool(product.complement_groups),
        "image_url": (
            f"/static/{product.primary_image.file_path}"
            if product.primary_image
            else None
        ),
        # Todas as fotos do produto, na ordem cadastrada — alimenta o
        # carrossel do modal de produto (ver "productImageIndex" em
        # store_menu.html). Cards/listagens continuam usando só
        # "image_url" (a principal), sem mudança de comportamento ali.
        # "color" é a cor média da borda da foto (ver _dominant_edge_color
        # em app/utils/uploads.py), usada como fundo sólido no carrossel
        # quando a caixa não bate com a proporção da imagem.
        "images": [
            {"url": f"/static/{img.file_path}", "color": img.dominant_color}
            for img in product.images
        ],
        "complement_groups": [
            {
                "id": group.id,
                "name": group.name,
                "is_variation": group.is_variation,
                "is_required": group.is_required,
                "single_choice": group.single_choice,
                "allow_quantity": group.allow_quantity,
                "options": [
                    {
                        "id": option.id,
                        "name": option.name,
                        "extra_price_cents": option.extra_price_cents,
                    }
                    for option in group.options
                    if option.is_active
                ],
            }
            for group in sorted(product.complement_groups, key=lambda g: g.display_order)
            if group.options
        ],
    }


def _build_menu_data(tenant):
    categories = (
        Category.query.filter_by(tenant_id=tenant.id, is_active=True)
        .options(
            selectinload(Category.products).selectinload(Product.images),
            selectinload(Category.products).selectinload(Product.complement_groups)
            .selectinload(ComplementGroup.options),
        )
        .order_by(Category.display_order, Category.name)
        .all()
    )

    menu_categories = []
    for category in categories:
        products = [p for p in category.products if p.is_active]
        products.sort(key=lambda p: (p.display_order, p.name))
        if not products:
            continue

        menu_categories.append(
            {
                "id": category.id,
                "name": category.name,
                "icon": category.icon or "other",
                "icon_image_url": f"/static/{category.icon_image_path}" if category.icon_image_path else None,
                "products": [_serialize_product(product) for product in products],
            }
        )

    # Produtos sem categoria (category_id NULL) não pertencem a nenhum
    # Category.products acima — sem isso, ficariam cadastrados mas
    # invisíveis para sempre no cardápio público. Agrupamos todos num
    # bucket sintético no fim do menu.
    #
    # Importante: o nome desse bucket precisa ser diferente de "Outros"
    # — esse é um nome comum para uma categoria de verdade que o lojista
    # cria (ex: "Outros" com adicionais avulsos). Se os dois se
    # chamassem igual, o cliente veria duas entradas idênticas na barra
    # lateral (a categoria real "Outros" e este bucket sintético), o que
    # parece um bug de duplicação mesmo não sendo.
    uncategorized = (
        Product.query.filter_by(tenant_id=tenant.id, category_id=None, is_active=True)
        .options(
            selectinload(Product.images),
            selectinload(Product.complement_groups).selectinload(ComplementGroup.options),
        )
        .order_by(Product.display_order, Product.name)
        .all()
    )
    if uncategorized:
        menu_categories.append(
            {
                "id": "uncategorized",
                "name": "Sem categoria",
                "icon": "uncategorized",
                "icon_image_url": None,
                "products": [_serialize_product(product) for product in uncategorized],
            }
        )

    address_parts = [
        f"{tenant.address_street}, {tenant.address_number}" if tenant.address_street and tenant.address_number
        else (tenant.address_street or ""),
        tenant.address_neighborhood or "",
        tenant.address_city or "",
    ]
    address_line = " — ".join(part for part in address_parts if part) or None

    service_labels = []
    if tenant.pickup_enabled:
        service_labels.append("Retirada")
    if tenant.delivery_enabled:
        service_labels.append("Entrega")
    service_line = " e ".join(service_labels) or None

    return {
        "tenant": {
            "name": tenant.name,
            "slug": tenant.slug,
            "delivery_enabled": tenant.delivery_enabled,
            "pickup_enabled": tenant.pickup_enabled,
            "delivery_fee_cents": tenant.delivery_fee_cents or 0,
            "free_delivery_above_cents": tenant.free_delivery_above_cents or 0,
            "min_order_cents": tenant.min_order_cents or 0,
            "show_price_from_label": tenant.show_price_from_label,
            "address_line": address_line,
            "service_line": service_line,
            "opening_status": tenant.opening_status(),
            "accepted_payment_methods": tenant.accepted_payment_methods,
            "notes_placeholder": (tenant.theme_settings or {}).get("notes_placeholder") or "Digite uma observação (opcional)",
        },
        "categories": menu_categories,
    }


@public_bp.route("/<slug>")
def store_home(slug):
    tenant = Tenant.query.filter_by(slug=slug).first()
    if tenant is None:
        abort(404)
    if tenant.status not in (TenantStatus.TRIAL, TenantStatus.ACTIVE):
        # Página amigável e específica de "loja indisponível" — não a
        # página genérica de 404 (que fala em "link" e confundiria quem
        # chegou aqui pelo link certo do cardápio) e, de propósito, sem
        # dizer POR QUE está indisponível (pendência de pagamento,
        # suspensão, etc.) — isso é assunto entre a loja e o Comanda, não
        # algo que o cliente final precise ou deva saber.
        return render_template("public/store_unavailable.html", tenant_name=tenant.name), 404
    g.tenant = tenant
    menu_data = _build_menu_data(tenant)

    from app.services.banner_service import BannerService

    banners = BannerService(tenant).repo.list_active_ordered()
    banners_data = [
        {
            "id": banner.id,
            "title": banner.title,
            "subtitle": banner.subtitle or "",
            "link_url": banner.link_url,
            "image_url": f"/static/{banner.image_path}",
            "dominant_color": banner.dominant_color,
        }
        for banner in banners
    ]

    default_theme = {"accent": "#E8A33D", "accent_dark": "#C97F1F", "accent_soft": "rgba(232,163,61,0.14)"}
    theme = tenant.public_theme_css_vars or default_theme
    theme["mode"] = (tenant.theme_settings or {}).get("mode", "dark")

    return render_template(
        "public/store_menu.html", tenant=tenant, menu_data=menu_data, banners=banners_data, theme=theme,
    )


@public_bp.route("/<slug>/pedido", methods=["POST"])
@csrf.exempt  # validado manualmente abaixo via cabeçalho X-CSRFToken
def create_order(slug):
    from flask import current_app

    if current_app.config.get("WTF_CSRF_ENABLED", True):
        try:
            validate_csrf(request.headers.get("X-CSRFToken", ""))
        except CSRFError:
            return jsonify({"error": "Sessão expirada, atualize a página e tente novamente."}), 400

    tenant = resolve_tenant_by_slug(slug)

    json_data = request.get_json(silent=True)
    if json_data is None:
        return jsonify({"error": "Payload inválido."}), 400

    schema = CheckoutSchema()
    try:
        payload = schema.load(json_data)
    except ValidationError as exc:
        return jsonify({"error": "Dados inválidos.", "details": exc.messages}), 400

    try:
        order = OrderService(tenant).create_order(payload)
    except OrderValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    message = build_order_message(order)
    whatsapp_url = build_whatsapp_url(tenant, message)

    return (
        jsonify(
            {
                "order_id": order.id,
                "order_number": order.order_number,
                "total_cents": order.total_cents,
                "whatsapp_url": whatsapp_url,
            }
        ),
        201,
    )


# --- "Login" opcional do cliente final, só pelo telefone (sem senha) ---
#
# Trade-off de segurança aceito conscientemente: não há verificação de
# posse do número (sem SMS/OTP) — quem souber o telefone de um cliente
# "entra" e vê o histórico de pedidos dele nesta loja. Isso não piora o
# modelo de ameaça existente: o telefone já não é verificado em nenhum
# lugar do sistema hoje (o checkout aceita qualquer telefone digitado,
# sem confirmar posse). É uma conveniência opcional para o cliente
# acompanhar os próprios pedidos, não uma tela de segurança de verdade.
#
# Por isso, também não criamos um Customer novo aqui — só reaproveitamos
# um que já exista (ou seja, só quem já fez pelo menos um pedido nesta
# loja consegue "entrar").


@public_bp.route("/<slug>/entrar", methods=["POST"])
@csrf.exempt  # validado manualmente abaixo via cabeçalho X-CSRFToken, igual create_order
@limiter.limit("10 per minute", methods=["POST"])  # mitiga força bruta de telefone
def customer_login(slug):
    tenant = resolve_tenant_by_slug(slug)

    if current_app.config.get("WTF_CSRF_ENABLED", True):
        try:
            validate_csrf(request.headers.get("X-CSRFToken", ""))
        except CSRFError:
            return jsonify({"error": "Sessão expirada, atualize a página e tente novamente."}), 400

    json_data = request.get_json(silent=True) or {}
    raw_phone = (json_data.get("phone") or "").strip()
    if not raw_phone:
        return jsonify({"error": "Informe um telefone."}), 400
    if len(raw_phone) > 20:
        return jsonify({"error": "Número inválido. Informe o telefone com DDD, como (67) 99999-9999."}), 400

    phone = normalize_br_phone(raw_phone)
    if phone is None:
        return jsonify({"error": "Número inválido. Informe o telefone com DDD, como (67) 99999-9999."}), 400

    customer = CustomerRepository(tenant.id).get_by_phone(phone)
    if customer is None:
        return jsonify({"error": "Nenhum pedido encontrado para esse telefone nesta loja."}), 404

    session["customer_phone"] = phone
    session["customer_tenant_id"] = tenant.id
    return jsonify({"name": customer.name, "phone": customer.phone})


PAYMENT_METHOD_LABELS = {"pix": "Pix", "card": "Cartão", "cash": "Dinheiro", "other": "Outro"}


def _order_address_lines(order) -> list[str]:
    """Endereço de entrega de UM pedido, já formatado em linhas prontas
    pra exibir — mesma composição usada em lojista/orders/detail.html,
    só que como lista (o painel do lojista usa <br> direto no HTML; aqui
    é uma API JSON consumida pelo Alpine.js do cardápio público)."""
    if not order.address_street:
        return []
    first_line = f"{order.address_street}, {order.address_number or 's/n'}"
    if order.address_complement:
        first_line += f" ({order.address_complement})"
    second_line = order.address_neighborhood or ""
    if order.address_city:
        second_line += f", {order.address_city}" if second_line else order.address_city
    lines = [first_line]
    if second_line:
        lines.append(second_line)
    if order.address_reference:
        lines.append(f"Referência: {order.address_reference}")
    return lines


@public_bp.route("/<slug>/meus-pedidos")
def customer_orders(slug):
    tenant = resolve_tenant_by_slug(slug)

    phone = session.get("customer_phone")
    if not phone or session.get("customer_tenant_id") != tenant.id:
        return jsonify({"logged_in": False, "orders": []})

    customer = CustomerRepository(tenant.id).get_by_phone(phone)
    if customer is None:
        return jsonify({"logged_in": False, "orders": []})

    orders = (
        Order.query.filter_by(tenant_id=tenant.id, customer_id=customer.id)
        .options(selectinload(Order.items).selectinload(OrderItem.choices))
        .order_by(Order.created_at.desc())
        .all()
    )
    return jsonify(
        {
            "logged_in": True,
            "name": customer.name,
            "phone": customer.phone,
            "orders": [
                {
                    "order_number": o.order_number,
                    "status": o.status.value,
                    "status_label": STATUS_LABELS.get(o.status.value, o.status.value),
                    "created_at": o.created_at.isoformat(),
                    "created_at_label": o.created_at.strftime("%d/%m/%Y às %H:%M"),
                    "delivery_type": o.delivery_type.value,
                    "address_lines": _order_address_lines(o),
                    "payment_label": PAYMENT_METHOD_LABELS.get(o.payment_method.value, "Outro"),
                    "subtotal_cents": o.subtotal_cents,
                    "delivery_fee_cents": o.delivery_fee_cents,
                    "total_cents": o.total_cents,
                    "notes": o.notes,
                    "items": [
                        {
                            "name": item.product_name,
                            "quantity": item.quantity,
                            "unit_price_cents": item.unit_price_cents,
                            "subtotal_cents": item.subtotal_cents,
                            "choices": [
                                f"{c.group_name}: {c.quantity}x {c.option_name}" if c.quantity > 1
                                else f"{c.group_name}: {c.option_name}"
                                for c in item.choices
                            ],
                        }
                        for item in o.items
                    ],
                }
                for o in orders
            ],
        }
    )


@public_bp.route("/<slug>/sair", methods=["POST"])
@csrf.exempt
def customer_logout(slug):
    session.pop("customer_phone", None)
    session.pop("customer_tenant_id", None)
    return jsonify({"ok": True})


def _get_logged_in_customer(tenant):
    """Cliente da sessão atual, escopado a esse tenant — usado pelas rotas
    de autoatendimento (editar dados / remover conta) abaixo. Retorna None
    se ninguém estiver "logado" (telefone) para esta loja."""
    phone = session.get("customer_phone")
    if not phone or session.get("customer_tenant_id") != tenant.id:
        return None
    return CustomerRepository(tenant.id).get_by_phone(phone)


@public_bp.route("/<slug>/meus-dados", methods=["POST"])
@csrf.exempt  # validado manualmente abaixo, igual create_order/customer_login
@limiter.limit("10 per minute", methods=["POST"])
def customer_update_profile(slug):
    tenant = resolve_tenant_by_slug(slug)

    if current_app.config.get("WTF_CSRF_ENABLED", True):
        try:
            validate_csrf(request.headers.get("X-CSRFToken", ""))
        except CSRFError:
            return jsonify({"error": "Sessão expirada, atualize a página e tente novamente."}), 400

    customer = _get_logged_in_customer(tenant)
    if customer is None:
        return jsonify({"error": "Você precisa entrar com seu telefone antes de editar seus dados."}), 401

    json_data = request.get_json(silent=True) or {}
    name = (json_data.get("name") or "").strip()
    raw_phone = (json_data.get("phone") or "").strip()

    if not name:
        return jsonify({"error": "Informe seu nome."}), 400
    if len(name) > 150:
        return jsonify({"error": "Nome muito longo."}), 400

    phone = normalize_br_phone(raw_phone)
    if phone is None:
        return jsonify({"error": "Número inválido. Informe o telefone com DDD, como (67) 99999-9999."}), 400

    if phone != customer.phone:
        conflict = CustomerRepository(tenant.id).get_by_phone(phone)
        if conflict is not None and conflict.id != customer.id:
            return jsonify({"error": "Já existe um cadastro com esse telefone nesta loja."}), 409

    customer.name = name
    customer.phone = phone
    try:
        db.session.commit()
    except IntegrityError:
        # Corrida: outra requisição concorrente "roubou" esse telefone
        # entre a checagem acima e o commit (ex.: duas abas do mesmo
        # cliente salvando ao mesmo tempo). A constraint
        # uq_customer_tenant_phone barra a duplicata no banco — devolve
        # um erro amigável em vez de 500.
        db.session.rollback()
        return jsonify({"error": "Já existe um cadastro com esse telefone nesta loja."}), 409

    session["customer_phone"] = phone
    return jsonify({"name": customer.name, "phone": customer.phone})


@public_bp.route("/<slug>/conta/remover", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute", methods=["POST"])
def customer_delete_account(slug):
    tenant = resolve_tenant_by_slug(slug)

    if current_app.config.get("WTF_CSRF_ENABLED", True):
        try:
            validate_csrf(request.headers.get("X-CSRFToken", ""))
        except CSRFError:
            return jsonify({"error": "Sessão expirada, atualize a página e tente novamente."}), 400

    customer = _get_logged_in_customer(tenant)
    if customer is None:
        return jsonify({"error": "Você precisa entrar com seu telefone antes de remover sua conta."}), 401

    # Os pedidos já feitos continuam no histórico do lojista (faturamento,
    # relatórios) — só perdem o vínculo com o cadastro removido, já que
    # Order.customer_id é nullable com ondelete=SET NULL.
    db.session.delete(customer)
    db.session.commit()

    session.pop("customer_phone", None)
    session.pop("customer_tenant_id", None)
    return jsonify({"ok": True})
