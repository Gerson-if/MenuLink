"""
Comandos de linha de comando customizados.

Uso:
    flask seed-db               -> cria super admin + plano + loja demo (idempotente)
    flask create-admin          -> cria (ou reseta a senha de) um super admin específico
    flask list-admins           -> lista os super admins cadastrados
    flask update-admin-email    -> troca o e-mail de um super admin existente
    flask remove-admin          -> remove um super admin (recusa remover o último)
    flask backfill-image-colors -> recalcula cor dominante e recomprime pra WEBP
                                    banners/fotos de produto enviados antes dessa
                                    mudança (idempotente, ver app/utils/uploads.py)

Estes comandos de gestão de Super Admin (create-admin, list-admins,
update-admin-email, remove-admin) são chamados também pelo menu interativo
de deploy/install.sh — mensagens de erro aqui aparecem direto pra quem
estiver usando o menu, então evite jargão de exceção do Python.
"""

import os
import uuid

import click
from flask import Flask, current_app

from app.extensions import db
from app.models import Banner, BillingCycle, Plan, ProductImage, Tenant, TenantStatus, User, UserRole


def register_cli(app: Flask) -> None:
    app.cli.add_command(seed_db)
    app.cli.add_command(create_admin)
    app.cli.add_command(list_admins)
    app.cli.add_command(update_admin_email)
    app.cli.add_command(remove_admin)
    app.cli.add_command(check_db)
    app.cli.add_command(backfill_image_colors)


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

    db.session.commit()
    click.echo("Seed concluído.")


@click.command("create-admin")
@click.option("--name", prompt=True)
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def create_admin(name, email, password):
    """Cria (ou reseta a senha de) um super administrador específico."""

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
