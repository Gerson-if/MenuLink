"""
Comandos de linha de comando customizados.

Uso:
    flask seed-db              -> cria super admin + plano + loja demo (idempotente)
    flask create-admin         -> cria (ou atualiza) um super admin específico
    flask backfill-image-colors -> recalcula cor dominante e recomprime pra WEBP
                                    banners/fotos de produto enviados antes dessa
                                    mudança (idempotente, ver app/utils/uploads.py)
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
