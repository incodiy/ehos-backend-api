"""Common response envelope schemas — every EHOS collection/detail endpoint returns
`{success, data, ...}` optionally with pagination meta, per openapi.yaml."""

import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")
TList = TypeVar("TList")

# Hybrid identity (ARD-007): public UUID or internal numeric id. Request
# payloads and path/query params accepting entity references use this so
# callers may pass either the public uuid or the legacy/internal BIGINT id
# (as int or stringified number) — resolution happens via get_by_uuid.
HybridId = int | uuid.UUID


class PaginationMeta(BaseModel):
    current_page: int = 1
    per_page: int = 50
    total: int = 0
    last_page: int = 1


class Envelope(BaseModel, Generic[T]):  # noqa: UP046 — Pydantic generic envelope
    success: bool = True
    message: str | None = None
    data: T


class Paginated(Envelope[T], Generic[T, TList]):  # noqa: UP046 — Pydantic generic envelope
    data: list[TList]
    meta: PaginationMeta