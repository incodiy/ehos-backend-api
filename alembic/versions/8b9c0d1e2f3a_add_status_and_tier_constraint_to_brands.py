"""add status and tier constraint to brands

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-09-16 11:42:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '8b9c0d1e2f3a'
down_revision: str | None = '7a8b9c0d1e2f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'brands',
        sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    )
    op.create_check_constraint(
        'chk_brands_status',
        'brands',
        "status IN ('ACTIVE', 'INACTIVE', 'RETIRED')"
    )
    op.create_check_constraint(
        'chk_brands_tier',
        'brands',
        "tier IN ('Luxury', 'Upscale', 'Boutique', 'Midscale', 'Budget', 'Eco-Resort')"
    )
    op.create_index(
        'ix_brands_status',
        'brands',
        ['status']
    )


def downgrade() -> None:
    op.drop_index('ix_brands_status', table_name='brands')
    op.drop_constraint('chk_brands_tier', 'brands', type_='check')
    op.drop_constraint('chk_brands_status', 'brands', type_='check')
    op.drop_column('brands', 'status')
