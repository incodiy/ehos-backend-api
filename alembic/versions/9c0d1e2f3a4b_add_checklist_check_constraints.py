"""add checklist check constraints and status index

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-09-16 22:30:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '9c0d1e2f3a4b'
down_revision: str | None = '8b9c0d1e2f3a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        'chk_checklist_templates_status',
        'checklist_templates',
        "status IN ('DRAFT', 'LOCKED', 'ARCHIVED')"
    )
    op.create_check_constraint(
        'chk_checklist_templates_dept',
        'checklist_templates',
        "department IN ('GM', 'HOUSEKEEPING', 'KITCHEN_FB', 'SECURITY_RISK')"
    )
    op.create_check_constraint(
        'chk_checklist_items_rubric',
        'checklist_items',
        "rubric_type IN ('TRAFFIC_LIGHT', 'NUMERIC_SCALE', 'MULTI_ROOM', 'BINARY_COUNT')"
    )
    op.create_index(
        'ix_checklist_templates_status',
        'checklist_templates',
        ['status']
    )


def downgrade() -> None:
    op.drop_index('ix_checklist_templates_status', table_name='checklist_templates')
    op.drop_constraint('chk_checklist_items_rubric', 'checklist_items', type_='check')
    op.drop_constraint('chk_checklist_templates_dept', 'checklist_templates', type_='check')
    op.drop_constraint('chk_checklist_templates_status', 'checklist_templates', type_='check')
