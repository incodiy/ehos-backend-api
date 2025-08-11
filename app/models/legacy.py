import uuid

UUIDT = uuid.UUID
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.mixins import AppendOnlyMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class LegacyIngestionBatch(Base, TimestampMixin):
    __tablename__ = "legacy_ingestion_batches"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    hotel_code: Mapped[str | None] = mapped_column(String(10))
    year: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="UPLOADED")
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_log: Mapped[dict | None] = mapped_column(JSONB)
    imported_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LegacyScoreRow(Base, AppendOnlyMixin):
    __tablename__ = "legacy_score_rows"
    __table_args__ = (
        Index("ix_legacy_batch", "batch_id"),
        Index("ix_legacy_hotel_period", "hotel_id", "period_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    batch_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("legacy_ingestion_batches.id"), nullable=False
    )
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    years: Mapped[int | None] = mapped_column(Integer)
    period_date: Mapped[date | None] = mapped_column(Date)
    main_category: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    sub_category: Mapped[str | None] = mapped_column(String(100))
    sub_category_child: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    totals: Mapped[dict | None] = mapped_column(JSONB)
    raw_csv: Mapped[dict | None] = mapped_column(JSONB)