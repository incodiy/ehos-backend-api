"""add master data hotel pilihan b (cities, hotel_contacts, ecommerce_region, hotel extensions)

Revision ID: d1e2f3a4b5c6
Revises: 9c0d1e2f3a4b
Create Date: 2026-09-19 04:50:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: str | None = '9c0d1e2f3a4b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Tambah ecommerce_region di tabel regions
    op.add_column('regions', sa.Column('ecommerce_region', sa.String(length=100), nullable=True))

    # 2. Buat tabel master cities
    op.create_table(
        'cities',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('uuid', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('province_id', sa.BigInteger(), sa.ForeignKey('provinces.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('region_id', sa.BigInteger(), sa.ForeignKey('regions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('ecommerce_city', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cities')),
        sa.UniqueConstraint('uuid', name=op.f('uq_cities_uuid')),
        sa.UniqueConstraint('name', 'province_id', name='uq_cities_name_province')
    )
    op.create_index('ix_cities_province', 'cities', ['province_id'])
    op.create_index('ix_cities_region', 'cities', ['region_id'])

    # 3. Tambah kolom city_id, image_url, has_fb, period_update di tabel hotels
    op.add_column('hotels', sa.Column('city_id', sa.BigInteger(), sa.ForeignKey('cities.id', ondelete='SET NULL'), nullable=True))
    op.add_column('hotels', sa.Column('image_url', sa.Text(), nullable=True))
    op.add_column('hotels', sa.Column('has_fb', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('hotels', sa.Column('period_update', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_hotels_city', 'hotels', ['city_id'])

    # 4. Buat tabel hotel_contacts (Direktori Kontak PIC GM, Sales, Finance, ROM)
    op.create_table(
        'hotel_contacts',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('uuid', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.BigInteger(), sa.ForeignKey('hotels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contact_type', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('is_primary', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_hotel_contacts')),
        sa.UniqueConstraint('uuid', name=op.f('uq_hotel_contacts_uuid')),
        sa.CheckConstraint("contact_type IN ('GM', 'SALES', 'FINANCE', 'ROM')", name='chk_hotel_contacts_type')
    )
    op.create_index('ix_hotel_contacts_hotel', 'hotel_contacts', ['hotel_id'])
    op.create_index('ix_hotel_contacts_hotel_type', 'hotel_contacts', ['hotel_id', 'contact_type'])


def downgrade() -> None:
    op.drop_table('hotel_contacts')
    op.drop_index('ix_hotels_city', table_name='hotels')
    op.drop_column('hotels', 'period_update')
    op.drop_column('hotels', 'has_fb')
    op.drop_column('hotels', 'image_url')
    op.drop_column('hotels', 'city_id')
    op.drop_table('cities')
    op.drop_column('regions', 'ecommerce_region')
