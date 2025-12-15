from __future__ import annotations

import uuid

UUIDT = uuid.UUID
from datetime import date, datetime
from typing import Any
from app.models.users import User

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")


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
    ecommerce_region: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")


class City(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "cities"
    __table_args__ = (
        UniqueConstraint("name", "province_id", name="uq_cities_name_province"),
        Index("ix_cities_province", "province_id"),
        Index("ix_cities_region", "region_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    province_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("provinces.id", ondelete="RESTRICT"), nullable=False)
    region_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False)
    ecommerce_city: Mapped[str | None] = mapped_column(String(100))

    province: Mapped["Province"] = relationship(foreign_keys=[province_id], lazy="selectin")
    region: Mapped["Region"] = relationship(foreign_keys=[region_id], lazy="selectin")


class Hotel(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hotels"
    __table_args__ = (
        Index("idx_hotels_geo", "geo", postgresql_using="gist"),
        Index("ix_hotels_region", "region_id"),
        Index("ix_hotels_brand", "brand_id"),
        Index("ix_hotels_city", "city_id"),
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
    city_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("cities.id", ondelete="SET NULL"))
    city: Mapped[str | None] = mapped_column(String(100))
    geo: Mapped[Any] = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=False))
    geofence_radius_meters: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    mice_facilities: Mapped[dict | None] = mapped_column(JSONB)
    gm_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    rom_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    opening_date: Mapped[date | None] = mapped_column(Date)
    terminate_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    image_url: Mapped[str | None] = mapped_column(Text)
    has_fb: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    period_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    brand: Mapped["Brand"] = relationship(foreign_keys=[brand_id], lazy="selectin")
    region: Mapped["Region"] = relationship(foreign_keys=[region_id], lazy="selectin")
    province: Mapped["Province"] = relationship(foreign_keys=[province_id], lazy="selectin")
    city_rel: Mapped["City | None"] = relationship(foreign_keys=[city_id], lazy="selectin")
    gm: Mapped["User | None"] = relationship(foreign_keys=[gm_id], lazy="selectin")
    rom: Mapped["User | None"] = relationship(foreign_keys=[rom_id], lazy="selectin")
    contacts: Mapped[list["HotelContact"]] = relationship(back_populates="hotel", lazy="selectin", cascade="all, delete-orphan")


class HotelContact(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hotel_contacts"
    __table_args__ = (
        Index("ix_hotel_contacts_hotel", "hotel_id"),
        Index("ix_hotel_contacts_hotel_type", "hotel_id", "contact_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False)
    contact_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    user_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    hotel: Mapped["Hotel"] = relationship(back_populates="contacts", foreign_keys=[hotel_id])
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id], lazy="selectin")


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