"""AWS Signature Version 4, by hand (D48): no SDK, no new dependency. Pure functions, no network.

Used by the S3-compatible media host (``platforms/s3_media_host.py``). Checked against AWS's published
test vectors (``tests/unit/test_sigv4.py``). Nothing here logs anything: a secret key or a signature never
leaves these functions except as the signature itself, which the caller treats as a secret.
"""

import hashlib
import hmac
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def uri_encode(text: str, *, keep_slash: bool = False) -> str:
    """Percent-encode everything except the unreserved characters (RFC 3986), as SigV4 requires."""
    return quote(text, safe="-_.~/" if keep_slash else "-_.~")


def canonical_query(params: list[tuple[str, str]]) -> str:
    """Each name and value encoded, then sorted by name (and value)."""
    encoded = sorted((uri_encode(name), uri_encode(value)) for name, value in params)
    return "&".join(f"{name}={value}" for name, value in encoded)


def canonical_request(
    method: str, path: str, params: list[tuple[str, str]], headers: dict[str, str], payload_hash: str
) -> tuple[str, str]:
    """The canonical request and its signed-headers list. ``path`` is already URI-encoded (S3: once)."""
    clean = {name.strip().lower(): " ".join(value.strip().split()) for name, value in headers.items()}
    names = sorted(clean)
    signed = ";".join(names)
    lines = [method, path or "/", canonical_query(params), *(f"{n}:{clean[n]}" for n in names), "", signed, payload_hash]
    return "\n".join(lines), signed


def scope(date: str, region: str, service: str) -> str:
    return f"{date}/{region}/{service}/aws4_request"


def string_to_sign(amz_date: str, credential_scope: str, canonical: str) -> str:
    return "\n".join([ALGORITHM, amz_date, credential_scope, sha256_hex(canonical.encode("utf-8"))])


def signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    key = _hmac(f"AWS4{secret_key}".encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def signature(secret_key: str, amz_date: str, region: str, service: str, canonical: str) -> str:
    date = amz_date[:8]
    text = string_to_sign(amz_date, scope(date, region, service), canonical)
    return hmac.new(signing_key(secret_key, date, region, service), text.encode("utf-8"), hashlib.sha256).hexdigest()


def authorization(
    access_key_id: str,
    secret_key: str,
    *,
    method: str,
    path: str,
    params: list[tuple[str, str]],
    headers: dict[str, str],
    payload_hash: str,
    amz_date: str,
    region: str,
    service: str,
) -> str:
    """The ``Authorization`` header value for a request whose ``headers`` (Host and X-Amz-Date included) are all signed."""
    canonical, signed = canonical_request(method, path, params, headers, payload_hash)
    sig = signature(secret_key, amz_date, region, service, canonical)
    return f"{ALGORITHM} Credential={access_key_id}/{scope(amz_date[:8], region, service)}, SignedHeaders={signed}, Signature={sig}"


def presigned_query(
    access_key_id: str,
    secret_key: str,
    *,
    method: str,
    host: str,
    path: str,
    amz_date: str,
    region: str,
    service: str,
    expires_s: int,
) -> str:
    """The query string of a presigned URL (only ``host`` signed, payload unsigned), signature last."""
    params = [
        ("X-Amz-Algorithm", ALGORITHM),
        ("X-Amz-Credential", f"{access_key_id}/{scope(amz_date[:8], region, service)}"),
        ("X-Amz-Date", amz_date),
        ("X-Amz-Expires", str(expires_s)),
        ("X-Amz-SignedHeaders", "host"),
    ]
    canonical, _ = canonical_request(method, path, params, {"host": host}, UNSIGNED_PAYLOAD)
    sig = signature(secret_key, amz_date, region, service, canonical)
    return f"{canonical_query(params)}&X-Amz-Signature={sig}"


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()
