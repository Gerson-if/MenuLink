"""adicionar sugestoes e reportes de erro (feedbacks)

Revision ID: c3d4e5f6a7b8
Revises: 381571c00001
Create Date: 2026-07-30 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedbacks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('submitted_by_user_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.Enum('suggestion', 'bug_report', name='feedback_kind'), nullable=False),
        sa.Column(
            'status', sa.Enum('new', 'in_review', 'resolved', 'archived', name='feedback_status'), nullable=False,
        ),
        sa.Column('name', sa.String(length=150), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('admin_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['submitted_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('feedbacks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feedbacks_tenant_id'), ['tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('feedbacks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feedbacks_tenant_id'))

    op.drop_table('feedbacks')
