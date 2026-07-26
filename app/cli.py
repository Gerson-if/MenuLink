"""
Comandos de linha de comando customizados.

Uso:
    flask seed-db               -> cria super admin + plano + loja demo com cardápio
                                    de exemplo (idempotente)
    flask create-admin          -> cria (ou reseta a senha de) um super admin específico
    flask list-admins           -> lista os super admins cadastrados
    flask update-admin-email    -> troca o e-mail de um super admin existente
    flask remove-admin          -> remove um super admin (recusa remover o último)
    flask backfill-image-colors -> recalcula cor dominante e recomprime pra WEBP
                                    banners/fotos de produto enviados antes dessa
                                    mudança (idempotente, ver app/utils/uploads.py)
    flask process-trials         -> manda lembretes de fim de teste, avisa quem
                                    entrou na tolerância extra e suspende quem
                                    esgotou o prazo (ver app/services/trial_service.py) —
                                    programe para rodar 1x/dia num cron do servidor

Estes comandos de gestão de Super Admin (create-admin, list-admins,
update-admin-email, remove-admin) são chamados também pelo menu interativo
de deploy/install.sh — mensagens de erro aqui aparecem direto pra quem
estiver usando o menu, então evite jargão de exceção do Python.
"""

import os
import uuid

import click
from flask import Flask, current_app
from slugify import slugify

from app.extensions import db
from app.models import (
    Banner,
    BillingCycle,
    Category,
    ComplementGroup,
    ComplementOption,
    Plan,
    Product,
    ProductImage,
    Tenant,
    TenantStatus,
    User,
    UserRole,
)
from app.utils.demo_media import generate_banner, generate_logo, generate_product_card, save_generated_image

# Categoria (pelo slug, ver _seed_demo_catalog) -> "glifo" usado para
# desenhar a foto de demonstração dos produtos dela (ver _seed_demo_media
# e app/utils/demo_media.py). Os ícones de categoria do sistema
# (CATEGORY_ICON_KEYS) são um conjunto fechado/genérico demais para
# variar bem numa churrascaria — por isso um mapeamento à parte aqui.
_DEMO_CATEGORY_GLYPHS = {
    "espetinhos": "espeto",
    "pratos-na-brasa": "prato",
    "acompanhamentos": "bowl",
    "bebidas": "cup",
    "sobremesas": "cupcake",
    "combos": "combo",
}


def register_cli(app: Flask) -> None:
    app.cli.add_command(seed_db)
    app.cli.add_command(create_admin)
    app.cli.add_command(list_admins)
    app.cli.add_command(update_admin_email)
    app.cli.add_command(remove_admin)
    app.cli.add_command(check_db)
    app.cli.add_command(backfill_image_colors)
    app.cli.add_command(process_trials)


@click.command("seed-db")
def seed_db():
    """Popula o banco com dados mínimos para começar a testar localmente."""

    # --- Super Admin ---
    admin = User.query.filter_by(email="admin@cardapio.saas").first()
    if not admin:
        admin = User(
            name="Super Administrador",
            email="admin@cardapio.saas",
            role=UserRole.SUPER_ADMIN,
            tenant_id=None,
        )
        admin.set_password("admin123")
        db.session.add(admin)
        click.echo("✔ Super admin criado: admin@cardapio.saas / admin123")
    else:
        click.echo("• Super admin já existia, pulando.")

    # --- Plano padrão ---
    plan = Plan.query.filter_by(slug="basico").first()
    if not plan:
        plan = Plan(
            name="Básico",
            slug="basico",
            description="Plano inicial para lojistas.",
            price_cents=4990,
            billing_cycle=BillingCycle.MONTHLY,
            max_products=50,
            max_categories=10,
        )
        db.session.add(plan)
        db.session.flush()
        click.echo("✔ Plano 'Básico' criado.")
    else:
        click.echo("• Plano 'Básico' já existia, pulando.")

    # --- Loja demo (migrando o cardápio original: Braseiro & Cia) ---
    tenant = Tenant.query.filter_by(slug="braseiro-cia").first()
    if not tenant:
        tenant = Tenant(
            name="Braseiro & Cia",
            slug="braseiro-cia",
            email="contato@braseiroecia.com.br",
            whatsapp_number="5567999999999",
            plan_id=plan.id,
            status=TenantStatus.ACTIVE,
            delivery_enabled=True,
            pickup_enabled=True,
        )
        db.session.add(tenant)
        db.session.flush()
        click.echo("✔ Loja demo 'Braseiro & Cia' criada.")

        owner = User(
            name="Dono do Braseiro & Cia",
            email="lojista@braseiroecia.com.br",
            role=UserRole.OWNER,
            tenant_id=tenant.id,
        )
        owner.set_password("lojista123")
        db.session.add(owner)
        click.echo("✔ Usuário lojista criado: lojista@braseiroecia.com.br / lojista123")
    else:
        click.echo("• Loja demo já existia, pulando.")

    # --- Horário de funcionamento demo ---
    # Idempotente e também serve de backfill (assim como o cardápio e as
    # imagens abaixo) — uma churrascaria abre à noite, fechada às
    # segundas. Preenchido pra explorar o status "aberto agora"/"fecha
    # em breve" do cardápio público (ver Tenant.opening_status).
    if not tenant.opening_hours:
        tenant.opening_hours = {
            "mon": {"closed": True},
            "tue": {"open": "18:00", "close": "23:00"},
            "wed": {"open": "18:00", "close": "23:00"},
            "thu": {"open": "18:00", "close": "23:00"},
            "fri": {"open": "18:00", "close": "23:30"},
            "sat": {"open": "12:00", "close": "23:30"},
            "sun": {"open": "12:00", "close": "22:00"},
        }
        click.echo("✔ Horário de funcionamento demo definido.")
    else:
        click.echo("• Loja demo já tinha horário de funcionamento, pulando.")

    # --- Cardápio demo ---
    # Roda tanto para uma loja recém-criada quanto para uma já existente
    # (ex: instalações antigas, de antes deste cardápio existir) — assim
    # rodar `flask seed-db` de novo também serve para "preencher" uma loja
    # demo que ainda estivesse sem categorias/produtos. Idempotente: só
    # popula se a loja ainda não tiver nenhuma categoria cadastrada.
    if Category.query.filter_by(tenant_id=tenant.id).first() is None:
        _seed_demo_catalog(tenant.id)
        click.echo("✔ Cardápio demo populado (categorias, produtos, variações e complementos).")
    else:
        click.echo("• Cardápio demo já tinha categorias, pulando.")

    db.session.commit()

    # --- Imagens de demonstração (logo, banners, fotos de produto) ---
    # Separado da checagem de categorias acima de propósito: quem já tinha
    # rodado `flask seed-db` antes desta funcionalidade existir tem a loja
    # demo com produtos mas SEM foto nenhuma — rodar de novo agora completa
    # só o que estiver faltando (logo, banners, foto de cada produto),
    # sem duplicar nada.
    _seed_demo_media(tenant)
    db.session.commit()
    click.echo("Seed concluído.")


def _seed_demo_media(tenant: Tenant) -> None:
    """Gera (se ainda não existirem) logo, banners promocionais e uma foto
    de demonstração para cada produto da loja 'Braseiro & Cia' — sem
    depender de fotos reais nem de fontes/emoji do sistema operacional
    (ver app/utils/demo_media.py). Cada parte é independentemente
    idempotente, então rodar de novo só completa o que estiver faltando."""

    if not tenant.logo_path:
        logo = generate_logo(tenant.name[:1].upper())
        relative_path, _color = save_generated_image(logo, os.path.join(f"tenant_{tenant.id}", "logo"))
        tenant.logo_path = relative_path
        click.echo("✔ Logo de demonstração gerada.")
    else:
        click.echo("• Loja já tinha logo, pulando.")

    products_without_image = [
        product for product in Product.query.filter_by(tenant_id=tenant.id).all() if not product.images
    ]
    if products_without_image:
        for product in products_without_image:
            category_slug = product.category.slug if product.category else None
            glyph = _DEMO_CATEGORY_GLYPHS.get(category_slug, "combo")
            card = generate_product_card(glyph, product.name)
            relative_path, color = save_generated_image(card, os.path.join(f"tenant_{tenant.id}", "products", str(product.id)))
            db.session.add(ProductImage(
                tenant_id=tenant.id,
                product_id=product.id,
                file_path=relative_path,
                dominant_color=color,
                is_primary=True,
                display_order=0,
            ))
        click.echo(f"✔ {len(products_without_image)} foto(s) de produto geradas.")
    else:
        click.echo("• Todos os produtos já tinham foto, pulando.")

    if Banner.query.filter_by(tenant_id=tenant.id).first() is None:
        banners = [
            ("espeto", "Bem-vindo ao Braseiro & Cia", "Sabor de brasa direto pra sua mesa"),
            ("combo", "Combo Família com 20% OFF", "Peça já pelo cardápio digital"),
            ("prato", "Peça pelo cardápio digital", "Entrega rápida e pagamento facilitado"),
        ]
        for order, (glyph, title, subtitle) in enumerate(banners):
            banner_image = generate_banner(glyph, title, subtitle)
            relative_path, color = save_generated_image(banner_image, os.path.join(f"tenant_{tenant.id}", "banners"))
            db.session.add(Banner(
                tenant_id=tenant.id,
                title=title,
                subtitle=subtitle,
                image_path=relative_path,
                dominant_color=color,
                display_order=order,
                is_active=True,
            ))
        click.echo(f"✔ {len(banners)} banner(s) de demonstração gerados.")
    else:
        click.echo("• Loja já tinha banners, pulando.")


def _seed_demo_catalog(tenant_id: int) -> None:
    """Popula um cardápio de demonstração realista para a loja 'Braseiro
    & Cia' (uma churrascaria fictícia): categorias, produtos com preço,
    selo e descrição, variações (ex: ponto da carne, tamanho) e grupos de
    complemento (ex: acompanhamentos), para servir de exemplo completo
    tanto do painel do lojista quanto do cardápio público."""

    def add_category(name: str, icon: str, order: int) -> Category:
        category = Category(
            tenant_id=tenant_id,
            name=name,
            slug=slugify(name),
            icon=icon,
            display_order=order,
            is_active=True,
        )
        db.session.add(category)
        db.session.flush()
        return category

    def add_product(category: Category, name: str, description: str, price_reais: float, order: int, tag: str | None = None) -> Product:
        product = Product(
            tenant_id=tenant_id,
            category_id=category.id,
            name=name,
            slug=slugify(name),
            description=description,
            price_cents=round(price_reais * 100),
            tag=tag,
            is_active=True,
            display_order=order,
        )
        db.session.add(product)
        db.session.flush()
        return product

    def add_group(product: Product, name: str, *, is_variation: bool, is_required: bool, single_choice: bool, allow_quantity: bool = False, max_choices: int | None = None) -> ComplementGroup:
        group = ComplementGroup(
            tenant_id=tenant_id,
            product_id=product.id,
            name=name,
            is_variation=is_variation,
            is_required=is_required,
            single_choice=single_choice,
            allow_quantity=allow_quantity,
            max_choices=max_choices,
            display_order=0,
        )
        db.session.add(group)
        db.session.flush()
        return group

    def add_option(group: ComplementGroup, name: str, extra_price_reais: float, order: int) -> ComplementOption:
        option = ComplementOption(
            tenant_id=tenant_id,
            group_id=group.id,
            name=name,
            extra_price_cents=round(extra_price_reais * 100),
            is_active=True,
            display_order=order,
        )
        db.session.add(option)
        return option

    def add_ponto_da_carne(product: Product) -> None:
        group = add_group(product, "Ponto da carne", is_variation=True, is_required=True, single_choice=True)
        for order, name in enumerate(["Mal passado", "Ao ponto", "Bem passado"]):
            add_option(group, name, 0, order)

    def add_acompanhamentos(product: Product) -> None:
        group = add_group(product, "Acompanhamentos extras", is_variation=False, is_required=False, single_choice=False, max_choices=3)
        for order, (name, price) in enumerate([("Farofa extra", 3), ("Vinagrete extra", 3), ("Pão de alho", 4)]):
            add_option(group, name, price, order)

    # --- Espetinhos ---
    espetinhos = add_category("Espetinhos", "combo", 0)
    p = add_product(espetinhos, "Espeto de Picanha", "Picanha nobre grelhada na brasa, temperada com sal grosso.", 18.0, 0, tag="Mais pedido")
    add_ponto_da_carne(p)
    add_acompanhamentos(p)
    p = add_product(espetinhos, "Espeto de Frango com Bacon", "Cubos de frango envoltos em bacon, grelhados no ponto.", 16.0, 1)
    add_acompanhamentos(p)
    p = add_product(espetinhos, "Espeto de Linguiça Toscana", "Linguiça artesanal grelhada na brasa.", 14.0, 2)
    p = add_product(espetinhos, "Espeto de Coração", "Coração de frango temperado e grelhado.", 15.0, 3)
    p = add_product(espetinhos, "Espeto de Queijo Coalho", "Queijo coalho grelhado com toque de mel e orégano.", 12.0, 4, tag="Vegetariano")

    # --- Pratos na Brasa ---
    pratos = add_category("Pratos na Brasa", "other", 1)
    p = add_product(pratos, "Picanha na Chapa", "Fatias generosas de picanha grelhadas na chapa, servidas com arroz e farofa.", 49.0, 0, tag="Mais pedido")
    add_ponto_da_carne(p)
    tamanho = add_group(p, "Tamanho da porção", is_variation=True, is_required=True, single_choice=True)
    add_option(tamanho, "300g (individual)", 0, 0)
    add_option(tamanho, "500g (para 2 pessoas)", 18, 1)
    p = add_product(pratos, "Costela Bovina Defumada", "Costela defumada por 8 horas, desmanchando no garfo.", 52.0, 1)
    p = add_product(pratos, "Frango Inteiro Desossado", "Frango desossado e grelhado inteiro na brasa, temperos da casa.", 38.0, 2)
    p = add_product(pratos, "Cupim ao Bafo", "Cupim cozido lentamente na brasa até ficar bem macio.", 45.0, 3)

    # --- Acompanhamentos ---
    acompanhamentos = add_category("Acompanhamentos", "other", 2)
    add_product(acompanhamentos, "Farofa Especial da Casa", "Farofa crocante com bacon e ovos.", 9.0, 0)
    add_product(acompanhamentos, "Vinagrete da Casa", "Tomate, cebola e pimentão picados, temperado com azeite.", 7.0, 1)
    add_product(acompanhamentos, "Arroz Carreteiro", "Arroz soltinho com carne seca desfiada.", 12.0, 2)
    add_product(acompanhamentos, "Mandioca Frita", "Porção de mandioca frita, crocante por fora e macia por dentro.", 15.0, 3)
    add_product(acompanhamentos, "Pão de Alho (4 unidades)", "Pão de alho grelhado na brasa.", 10.0, 4)

    # --- Bebidas ---
    bebidas = add_category("Bebidas", "drink", 3)
    add_product(bebidas, "Coca-Cola Lata 350ml", "Bem gelada.", 6.0, 0)
    add_product(bebidas, "Guaraná Antarctica Lata 350ml", "Bem gelado.", 6.0, 1)
    add_product(bebidas, "Suco Natural de Laranja 500ml", "Feito na hora.", 9.0, 2)
    add_product(bebidas, "Água Mineral 500ml", "Com ou sem gás.", 4.0, 3)
    add_product(bebidas, "Cerveja Long Neck", "Bem gelada, direto da geladeira.", 9.0, 4, tag="Gelada")
    add_product(bebidas, "Caipirinha de Limão", "Feita com cachaça artesanal.", 18.0, 5)

    # --- Sobremesas ---
    sobremesas = add_category("Sobremesas", "dessert", 4)
    add_product(sobremesas, "Pudim de Leite", "Receita tradicional da casa.", 9.0, 0)
    add_product(sobremesas, "Abacaxi na Brasa com Canela", "Abacaxi grelhado na brasa, polvilhado com canela e açúcar.", 11.0, 1, tag="Novidade")
    p = add_product(sobremesas, "Sorvete (2 bolas)", "Sorvete cremoso, escolha o sabor.", 12.0, 2)
    sabor = add_group(p, "Sabor", is_variation=True, is_required=True, single_choice=True)
    for order, name in enumerate(["Chocolate", "Morango", "Creme"]):
        add_option(sabor, name, 0, order)

    # --- Combos ---
    combos = add_category("Combos", "combo", 5)
    add_product(
        combos, "Combo Casal",
        "2 espetos à sua escolha + 2 bebidas + 1 acompanhamento — ideal para dividir.",
        69.0, 0, tag="Promoção",
    )
    add_product(
        combos, "Combo Família",
        "4 espetos + farofa + vinagrete + 4 bebidas — serve até 4 pessoas.",
        129.0, 1, tag="Promoção",
    )


@click.command("create-admin")
@click.option("--name", prompt=True)
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def create_admin(name, email, password):
    """Cria (ou reseta a senha de) um super administrador específico."""

    # bcrypt só aceita até 72 bytes de senha (ver app/models/user.py) —
    # sem esta checagem, um `--password` grande demais faz set_password
    # levantar ValueError e o comando quebrar com traceback do Python na
    # tela de quem estiver usando o menu do install.sh.
    if len(password.encode("utf-8")) > User._MAX_PASSWORD_BYTES:
        click.echo(f"✘ Senha muito longa (máximo {User._MAX_PASSWORD_BYTES} bytes). Escolha uma senha mais curta.")
        raise SystemExit(1)

    admin = User.query.filter_by(email=email).first()
    if admin:
        admin.set_password(password)
        admin.role = UserRole.SUPER_ADMIN
        admin.tenant_id = None
        click.echo(f"✔ Senha atualizada para o super admin existente: {email}")
    else:
        admin = User(name=name, email=email, role=UserRole.SUPER_ADMIN, tenant_id=None)
        admin.set_password(password)
        db.session.add(admin)
        click.echo(f"✔ Super admin criado: {email}")

    db.session.commit()


@click.command("list-admins")
def list_admins():
    """Lista os super admins cadastrados (id, nome, e-mail, criado em)."""

    admins = User.query.filter_by(role=UserRole.SUPER_ADMIN).order_by(User.id).all()
    if not admins:
        click.echo("Nenhum super admin cadastrado ainda. Use 'flask create-admin' para criar o primeiro.")
        return

    click.echo(f"{len(admins)} super admin(s) cadastrado(s):")
    for admin in admins:
        criado_em = admin.created_at.strftime("%d/%m/%Y %H:%M") if admin.created_at else "—"
        click.echo(f"  [{admin.id}] {admin.name} <{admin.email}> — criado em {criado_em}")


@click.command("update-admin-email")
@click.option("--old", "old_email", prompt="E-mail atual do super admin")
@click.option("--new", "new_email", prompt="Novo e-mail")
def update_admin_email(old_email, new_email):
    """Troca o e-mail de um super admin existente (mantém senha e histórico)."""

    old_email = old_email.strip().lower()
    new_email = new_email.strip().lower()

    if not new_email or "@" not in new_email:
        raise click.ClickException(f"'{new_email}' não parece um e-mail válido.")

    admin = User.query.filter_by(email=old_email, role=UserRole.SUPER_ADMIN).first()
    if not admin:
        raise click.ClickException(
            f"Nenhum super admin encontrado com o e-mail '{old_email}'. Use 'flask list-admins' para ver os cadastrados."
        )

    if old_email == new_email:
        click.echo("O novo e-mail é igual ao atual — nada para fazer.")
        return

    if User.query.filter_by(email=new_email).first():
        raise click.ClickException(f"Já existe um usuário cadastrado com o e-mail '{new_email}'. Escolha outro.")

    admin.email = new_email
    db.session.commit()
    click.echo(f"✔ E-mail atualizado: {old_email} -> {new_email}")


@click.command("remove-admin")
@click.option("--email", prompt="E-mail do super admin a remover")
@click.confirmation_option(
    prompt="Tem certeza que quer remover este super admin? Essa ação não pode ser desfeita.",
)
def remove_admin(email):
    """Remove um super admin. Recusa remover o último (a plataforma sempre
    precisa de pelo menos um, senão ninguém mais consegue gerenciá-la)."""

    email = email.strip().lower()
    admin = User.query.filter_by(email=email, role=UserRole.SUPER_ADMIN).first()
    if not admin:
        raise click.ClickException(
            f"Nenhum super admin encontrado com o e-mail '{email}'. Use 'flask list-admins' para ver os cadastrados."
        )

    total_admins = User.query.filter_by(role=UserRole.SUPER_ADMIN).count()
    if total_admins <= 1:
        raise click.ClickException(
            "Este é o único super admin cadastrado — não é possível remover, senão ninguém mais"
            " consegue gerenciar a plataforma. Crie outro super admin antes ('flask create-admin')."
        )

    db.session.delete(admin)
    db.session.commit()
    click.echo(f"✔ Super admin removido: {email}")


@click.command("check-db")
def check_db():
    """Confirma que a aplicação consegue conversar com o banco configurado
    em DATABASE_URL. Usado pelo menu de verificações de deploy/install.sh —
    imprime 'OK' e sai com código 0 quando a conexão funciona, ou uma
    mensagem de erro (código != 0) quando não."""

    from sqlalchemy import text

    try:
        db.session.execute(text("SELECT 1"))
        click.echo("OK")
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexão deve virar uma mensagem clara
        raise click.ClickException(f"Não consegui conectar ao banco de dados: {exc}")


@click.command("backfill-image-colors")
def backfill_image_colors():
    """Reprocessa banners/fotos de produto enviados antes da mudança de
    cor dominante + WEBP (ver app/utils/uploads.py): calcula a cor média
    da borda e recomprime pra WEBP quem ainda estiver em outro formato.
    Idempotente — pula quem já tem `dominant_color` preenchido."""
    from PIL import Image

    from app.utils.uploads import dominant_edge_color

    def _reprocess(obj, path_attr: str) -> bool:
        relative_path = getattr(obj, path_attr)
        absolute_path = os.path.join(current_app.static_folder, relative_path)
        if not os.path.isfile(absolute_path):
            click.echo(f"  ! arquivo não encontrado, pulando: {relative_path}")
            return False

        image = Image.open(absolute_path).convert("RGB")
        obj.dominant_color = dominant_edge_color(image)

        if not relative_path.lower().endswith(".webp"):
            new_relative = os.path.join(
                os.path.dirname(relative_path), f"{uuid.uuid4().hex}.webp"
            ).replace(os.sep, "/")
            new_absolute = os.path.join(current_app.static_folder, new_relative)
            image.save(new_absolute, format="WEBP", quality=82, method=6)
            os.remove(absolute_path)
            setattr(obj, path_attr, new_relative)

        return True

    count = 0
    for banner in Banner.query.filter(Banner.dominant_color.is_(None)).all():
        if _reprocess(banner, "image_path"):
            count += 1
    for product_image in ProductImage.query.filter(ProductImage.dominant_color.is_(None)).all():
        if _reprocess(product_image, "file_path"):
            count += 1

    db.session.commit()
    click.echo(f"OK: {count} imagem(ns) reprocessada(s).")


@click.command("process-trials")
def process_trials():
    """Processa o ciclo de vida do período de teste grátis de todas as
    lojas em TRIAL: manda lembrete automático (pelo chat de suporte)
    perto do fim do prazo, avisa quem acabou de entrar na tolerância
    extra, e suspende (BLOCKED_PAYMENT) quem esgotou a tolerância sem
    regularizar. Sem efeito sobre lojas que já saíram de TRIAL (ativas,
    suspensas, bloqueadas ou canceladas manualmente).

    Não há agendador embutido na aplicação — programe este comando para
    rodar uma vez por dia num cron/systemd timer do servidor (ver
    README, seção de deploy)."""
    from app.services.trial_service import TrialService

    summary = TrialService().process_all()
    click.echo(
        f"OK: {summary['reminders_sent']} lembrete(s), "
        f"{summary['d2_notices_sent']} aviso(s) de 2 dias, "
        f"{summary['grace_notices_sent']} aviso(s) de tolerância, "
        f"{summary['final_notices_sent']} aviso(s) de dia da suspensão, "
        f"{summary['suspended']} loja(s) suspensa(s) por teste expirado."
    )
