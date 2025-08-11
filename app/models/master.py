from __future__ import annotations

import uuid

UUIDT = uuid.UUID
from datetime import date
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class Brand(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(30), nullable=False)


class Province(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "provinces"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Region(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str | None] = mapped_column(String(50))
    sales_region: Mapped[str | None] = mapped_column(String(100))


class Hotel(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hotels"
    __table_args__ = (
        Index("idx_hotels_geo", "geo", postgresql_using="gist"),
        Index("ix_hotels_region", "region_id"),
        Index("ix_hotels_brand", "brand_id"),
        Index("ix_hotels_status", "status"),
        Index("ix_hotels_mice_facilities", "mice_facilities", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("brands.id"), nullable=False)
    region_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("regions.id"), nullable=False)
    province_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("provinces.id"), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    geo: Mapped[Any] = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=False))
    geofence_radius_meters: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    mice_facilities: Mapped[dict | None] = mapped_column(JSONB)
    gm_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    rom_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    opening_date: Mapped[date | None] = mapped_column(Date)
    terminate_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    brand: Mapped["Brand"] = relationship(foreign_keys=[brand_id], lazy="selectin")
    region: Mapped["Region"] = relationship(foreign_keys=[region_id], lazy="selectin")
    province: Mapped["Province"] = relationship(foreign_keys=[province_id], lazy="selectin")
    gm: Mapped["User | None"] = relationship(foreign_keys=[gm_id], lazy="selectin")
    rom: Mapped["User | None"] = relationship(foreign_keys=[rom_id], lazy="selectin")


class HotelDepartment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hotel_departments"
    __table_args__ = (UniqueConstraint("hotel_id", "code"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hod_user_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    hotel: Mapped["Hotel"] = relationship(foreign_keys=[hotel_id], lazy="selectin")
    hod_user: Mapped["User | None"] = relationship(foreign_keys=[hod_user_id], lazy="selectin")