"""MinIO/S3 presigned URL + object verify (task 7c, Constraint C2 / ARD-005 split-path).

Split-path media sync: jalur teks langsung ke Postgres; media lewat object
storage. Klien mendapat **presigned PUT URL** (expire 7 mnt), PUT 1-per-1, lalu
`confirm` di-backend memverifikasi reachability + ukuran + mime via presigned
GET (Range bytes=0-0). Verifikasi konten sha256 penuh dilakukan worker media
(pipeline SYSTEM.md §5); di sini skala verifikasi = C2 instant tanpa payload
raksasa.

Tidak memakai dependency baru: SigV4 query-signing diimplementasikan inline
(hmac/hashlib) dari config `EHOS_MINIO_*` — deterministik & testable tanpa
network. `verify_object` melakukan HEAD/Range GET ke MinIO; jaringan gagal →
`StorageUnavailable` (API menjawab 503 jujur, Constraint G4 — bukan data palsu).
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from app.core.config import settings

SERVICE = "s3"
REGION = "us-east-1"
PRESIGN_EXPIRES = 420  # 7 menit (SYSTEM.md §5.2)


class StorageUnavailableError(RuntimeError):
    """Object store tidak terjangkau — jangan di-mask jadi status palsu (G4)."""


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str) -> bytes:
    key = ("AWS4" + secret).encode()
    for part in (date_stamp, REGION, SERVICE, "aws4_request"):
        key = _hmac_sha256(key, part.encode())
    return key


def _base_url() -> str:
    scheme = "https" if settings.minio_secure else "http"
    return f"{scheme}://{settings.minio_endpoint}"


def _query_params(method: str, date_stamp: str, amz_date: str, content_type: str | None) -> tuple[str, dict]:
    signed_headers = "host;content-type" if content_type else "host"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": (
            f"{settings.minio_access_key}/{date_stamp}/{REGION}/{SERVICE}/aws4_request"
        ),
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(PRESIGN_EXPIRES),
        "X-Amz-SignedHeaders": signed_headers,
    }
    return signed_headers, params


def _sign(method: str, object_key: str, content_type: str | None, *, now: datetime | None = None) -> str:
    """Bangun presigned URL SigV4 query-string (murni — tanpa network)."""
    now = now or datetime.now(UTC)
    date_stamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    signed_headers, params = _query_params(method, date_stamp, amz_date, content_type)

    canonical_query = urllib.parse.urlencode(sorted(params.items()))
    canonical_uri = f"/{settings.minio_bucket}/{object_key}"
    canonical_request = "\n".join([
        method,
        canonical_uri,
        canonical_query,
        f"host:{settings.minio_endpoint}",
        f"content-type:{content_type}" if content_type else "",
        "",
        signed_headers,
        "UNSIGNED-PAYLOAD",
    ]).replace("\n\n\n", "\n\n")
    payload_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        f"{date_stamp}/{REGION}/{SERVICE}/aws4_request",
        payload_hash,
    ])
    signature = hmac.new(
        _signing_key(settings.minio_secret_key, date_stamp),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{_base_url()}/{settings.minio_bucket}/{object_key}?{canonical_query}&X-Amz-Signature={signature}"


def presigned_put_url(object_key: str, content_type: str | None = None, *, now: datetime | None = None) -> str:
    return _sign("PUT", object_key, content_type, now=now)


def presigned_get_url(object_key: str, *, now: datetime | None = None) -> str:
    return _sign("GET", object_key, None, now=now)


def upload_bytes(object_key: str, content: bytes, content_type: str) -> None:
    """Upload byte langsung (server-side) ke object store via presigned PUT.

    Dipakai utk artefak server-generated (PDF quotation, laporan). Gagal
    jaringan/objek tak terjangkau → `StorageUnavailableError` (API → 503 jujur,
    Constraint G4 — bukan data palsu).
    """
    url = presigned_put_url(object_key, content_type)
    req = urllib.request.Request(
        url,
        data=content,
        headers={"Content-Type": content_type},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — URL dari key WHITELIST internal
            if resp.status not in (200, 201, 204):
                raise StorageUnavailableError(f"Upload object gagal (HTTP {resp.status})")
    except StorageUnavailableError:
        raise
    except Exception as exc:
        raise StorageUnavailableError(f"Object store tidak terjangkau: {exc}") from exc


def verify_object(object_key: str, expected_size: int | None, expected_mime: str | None) -> bool:
    """Verifikasi reachability + ukuran + mime object di MinIO (Range GET 1 byte).

    True  → object ada, ukuran cocok, content-type cocok.
    False → object tidak cocok (→ upload_status FAILED).
    Storage tak terjangkau → StorageUnavailableError (API → 503 jujur).
    """
    url = presigned_get_url(object_key)
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL dari key WHITELIST internal
            status = resp.status
            if status not in (200, 206):
                return False
            content_range = resp.headers.get("Content-Range", "")
            length = resp.headers.get("Content-Length", "0")
            total = None
            if "/" in content_range:
                total = int(content_range.rsplit("/", 1)[1])
            elif status == 206:
                total = int(length)
            if expected_size is not None and total not in (None, expected_size):
                return False
            if expected_mime and expected_mime not in (resp.headers.get("Content-Type") or ""):
                return False
            return True
    except StorageUnavailableError:
        raise
    except Exception as exc:
        raise StorageUnavailableError(f"Object store tidak terjangkau: {exc}") from exc