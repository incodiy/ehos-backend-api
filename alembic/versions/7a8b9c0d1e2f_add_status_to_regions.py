"""add status to regions

Revision ID: 7a8b9c0d1e2f
Revises: 561c3026e315
Create Date: 2026-09-16 11:15:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '7a8b9c0d1e2f'
down_revision: str | None = '561c3026e315'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'regions',
        sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    )
    op.create_check_constraint(
        'chk_regions_status',
        'regions',
        "status IN ('ACTIVE', 'INACTIVE', 'RETIRED')"
    )
    op.create_index(
        'ix_regions_status',
        'regions',
        ['status']
    )


def downgrade() -> None:
    op.drop_index('ix_regions_status', table_name='regions')
    op.drop_constraint('chk_regions_status', 'regions', type_='check')
    op.drop_column('regions', 'status')
