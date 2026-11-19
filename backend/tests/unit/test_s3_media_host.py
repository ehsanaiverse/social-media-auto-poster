"""The S3-compatible media host (D48, Backblaze B2) against httpx.MockTransport: no real network.

The secret key, signatures and presigned URLs must never appear in a repr, a log line or an error text.
"""

import logging
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from social_media_auto_poster.platforms import sigv4
from social_media_auto_poster.platforms.media_host import MediaHostError, new_media_key
from social_media_auto_poster.platforms.s3_media_host import S3Config, S3MediaHost, load_s3_config

SECRET = "B2-SECRET-must-never-leak-9c1e"
KEY_ID = "0040000000000000000000001"
ENV = {
    "MEDIA_S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
    "MEDIA_S3_REGION": "us-west-004",
    "MEDIA_S3_ACCESS_KEY_ID": KEY_ID,
    "MEDIA_S3_SECRET_ACCESS_KEY": SECRET,
    "MEDIA_S3_BUCKET": "autoposter-media",
}
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class FakeBucket:
    """A tiny S3 that checks every request's signature with its own copy of the secret."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.answer: httpx.Response | None = None
        self.page_size = 1000

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert self._signature_ok(request), "bad signature"
        if self.answer is not None:
            return self.answer
        path = request.url.path
        bucket, _, key = path.lstrip("/").partition("/")
        assert bucket == "autoposter-media"
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            return httpx.Response(204 if self.objects.pop(key, None) is not None else 404)
        if request.method == "GET" and not key:
            return self._list(dict(parse_qsl(request.url.query.decode())))
        return httpx.Response(400)

    def _list(self, query: dict) -> httpx.Response:
        keys = sorted(k for k in self.objects if k.startswith(query["prefix"]))
        start = int(query.get("continuation-token", "0"))
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        body = ['<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
                f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>",
                *(f"<Contents><Key>{k}</Key></Contents>" for k in page)]
        if more:
            body.append(f"<NextContinuationToken>{start + self.page_size}</NextContinuationToken>")
        return httpx.Response(200, content="".join(body + ["</ListBucketResult>"]).encode())

    @staticmethod
    def _signature_ok(request: httpx.Request) -> bool:
        auth = request.headers["authorization"]
        signed_names = auth.split("SignedHeaders=")[1].split(",")[0].split(";")
        headers = {name: request.headers[name] for name in signed_names}
        params = parse_qsl(request.url.query.decode(), keep_blank_values=True)
        expected = sigv4.authorization(
            KEY_ID, SECRET, method=request.method, path=request.url.raw_path.split(b"?")[0].decode(), params=params,
            headers=headers, payload_hash=request.headers["x-amz-content-sha256"],
            amz_date=request.headers["x-amz-date"], region="us-west-004", service="s3",
        )
        return expected == auth and request.headers["x-amz-content-sha256"] == sigv4.sha256_hex(request.content)


@pytest.fixture
def bucket():
    return FakeBucket()


@pytest.fixture
def host(tmp_path, bucket):
    return S3MediaHost(load_s3_config(tmp_path, ENV), transport=bucket.transport(), now=lambda: NOW)


def test_put_delete_and_leftovers(host, bucket):
    key = new_media_key()
    host.put(key, JPEG, content_type="image/jpeg")
    assert bucket.objects == {key: JPEG}
    assert bucket.requests[0].url.path == f"/autoposter-media/{key}"  # Path-style.
    assert host.leftovers() == (key,)

    host.delete(key)
    host.delete(key)  # Already gone: not an error.
    assert host.leftovers() == ()


def test_leftovers_follow_continuation_tokens(host, bucket):
    bucket.page_size = 2
    keys = sorted(new_media_key() for _ in range(5))
    bucket.objects = {k: b"x" for k in keys} | {"other/kept.jpg": b"x"}
    assert host.leftovers() == tuple(keys)
    assert len(bucket.requests) == 3


def test_presigned_url_is_valid_for_one_hour_and_sends_nothing(host, bucket):
    url = host.url("ig-tmp/abc.jpg")
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "s3.us-west-004.backblazeb2.com", "/autoposter-media/ig-tmp/abc.jpg")
    assert query["X-Amz-Expires"] == "3600" and query["X-Amz-SignedHeaders"] == "host"
    assert query["X-Amz-Credential"] == f"{KEY_ID}/20261119/us-west-004/s3/aws4_request"
    expected = sigv4.presigned_query(KEY_ID, SECRET, method="GET", host=parts.netloc, path=parts.path,
                                     amz_date="20261119T120000Z", region="us-west-004", service="s3", expires_s=3600)
    assert parts.query == expected
    assert SECRET not in url and bucket.requests == []


def test_a_refusal_keeps_the_code_but_never_the_message(host, bucket):
    bucket.answer = httpx.Response(403, content=(
        f"<Error><Code>AccessDenied</Code><Message>key {SECRET} sig=deadbeef</Message></Error>").encode())
    with pytest.raises(MediaHostError) as raised:
        host.put("ig-tmp/a.jpg", JPEG, content_type="image/jpeg")
    assert str(raised.value) == "upload refused (HTTP 403, AccessDenied)"


def test_a_network_error_names_only_its_type(tmp_path):
    def fail(request):
        raise httpx.ConnectError(f"cannot reach {request.url} with {request.headers['authorization']}")

    host = S3MediaHost(load_s3_config(tmp_path, ENV), transport=httpx.MockTransport(fail), now=lambda: NOW)
    with pytest.raises(MediaHostError) as raised:
        host.delete("ig-tmp/a.jpg")
    assert str(raised.value) == "delete failed: ConnectError"
    assert raised.value.__suppress_context__


def test_secrets_never_reach_reprs_or_logs(tmp_path, host, bucket, caplog):
    caplog.set_level(logging.DEBUG)
    host.put("ig-tmp/a.jpg", JPEG, content_type="image/jpeg")
    host.url("ig-tmp/a.jpg")
    host.leftovers()
    signature = bucket.requests[0].headers["authorization"].split("Signature=")[1]
    config = load_s3_config(tmp_path, ENV)
    for text in (repr(host), repr(config), caplog.text):
        assert SECRET not in text and signature not in text and KEY_ID not in text


def test_settings_are_checked_by_name(tmp_path):
    with pytest.raises(MediaHostError, match="set MEDIA_S3_SECRET_ACCESS_KEY, MEDIA_S3_BUCKET"):
        load_s3_config(tmp_path, {**ENV, "MEDIA_S3_SECRET_ACCESS_KEY": " ", "MEDIA_S3_BUCKET": ""})
    with pytest.raises(MediaHostError, match="https://"):
        load_s3_config(tmp_path, {**ENV, "MEDIA_S3_ENDPOINT": "http://s3.example.com"})
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in ENV.items()), encoding="utf-8")
    assert load_s3_config(tmp_path, None).bucket == "autoposter-media"


def test_a_key_that_is_not_plain_is_refused_before_sending(host, bucket):
    for key in ("../x.jpg", "ig-tmp/a b.jpg", ""):
        with pytest.raises(MediaHostError, match="not a plain key"):
            host.put(key, JPEG, content_type="image/jpeg")
    assert bucket.requests == []
    assert isinstance(S3Config, type)
