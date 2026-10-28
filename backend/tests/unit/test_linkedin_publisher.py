"""The LinkedIn publisher against a fake LinkedIn (httpx.MockTransport). No network."""

import json
from pathlib import Path

import httpx
import pytest

from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PostContent
from social_media_auto_poster.platforms.linkedin.caption import MAX_CAPTION_LENGTH
from social_media_auto_poster.platforms.linkedin.client import LINKEDIN_VERSION, LinkedInClient
from social_media_auto_poster.platforms.linkedin.publisher import LinkedInPublisher, PublishOutcomeUnknown

TOKEN = "TEST-TOKEN-must-never-leak-7f3a"
IMAGE_URN = "urn:li:image:C4E10AQFoyyAjHPMQuQ"
UPLOAD_URL = "https://www.linkedin.com/dms-uploads/C4E10AQFoyyAjHPMQuQ/uploaded-image/0?ca=vector&ut=abc"
POST_ID = "urn:li:share:6844785523593134080"
IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "exif-9x4.jpg").read_bytes()


class FakeLinkedIn:
    """Answers each LinkedIn call; ``post`` decides what POST /rest/posts does."""

    def __init__(self, *, post=None, image_statuses=("AVAILABLE",), userinfo=None, initialize=None,
                 upload=None, image=None, upload_url=UPLOAD_URL):
        self.post = post if post is not None else httpx.Response(201, headers={"x-restli-id": POST_ID})
        self.image_statuses = list(image_statuses)
        self.userinfo = userinfo
        self.initialize = initialize
        self.upload = upload
        self.image = image
        self.upload_url = upload_url
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = (request.method, request.url.path)
        if route == ("GET", "/v2/userinfo"):
            return self._answer(self.userinfo, request) or httpx.Response(200, json={"sub": "abc123", "name": "X"})
        if route == ("POST", "/rest/images"):
            return self._answer(self.initialize, request) or httpx.Response(
                200, json={"value": {"uploadUrl": self.upload_url, "image": IMAGE_URN, "uploadUrlExpiresAt": 1}})
        if request.method == "PUT":
            return self._answer(self.upload, request) or httpx.Response(201)
        if request.method == "GET" and request.url.path.startswith("/rest/images/"):
            answer = self._answer(self.image, request)
            if answer is not None:
                return answer
            status = self.image_statuses.pop(0) if len(self.image_statuses) > 1 else self.image_statuses[0]
            return httpx.Response(200, json={"id": IMAGE_URN, "status": status})
        if route == ("POST", "/rest/posts"):
            return self._answer(self.post, request)
        raise AssertionError(f"unexpected request {route}")

    def _answer(self, behaviour, request):
        if behaviour is None:
            return None
        if isinstance(behaviour, BaseException):
            raise behaviour
        if isinstance(behaviour, type) and issubclass(behaviour, httpx.TransportError):
            raise behaviour("simulated", request=request)
        return behaviour

    def calls(self, method: str, path_start: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.startswith(path_start)]


def make_post(caption: str = "Launch (beta) #news", media: bytes = JPEG) -> PostContent:
    candidate = PostCandidate(Platform.LINKEDIN, "launch", Path("launch.jpg"), Path("launch.txt"))
    return PostContent(candidate=candidate, media_sha256="m" * 64, caption_sha256="c" * 64, media=media, caption=caption)


def publish(fake: FakeLinkedIn, post: PostContent | None = None, **options):
    sleeps = []
    with LinkedInClient(TOKEN, transport=httpx.MockTransport(fake)) as client:
        publisher = LinkedInPublisher(client, sleep=sleeps.append, **options)
        result = publisher.publish(post or make_post())
    return result, sleeps


# The successful flow


def test_successful_post_runs_every_step_in_order_and_returns_the_post_id():
    fake = FakeLinkedIn()

    result, _ = publish(fake)

    assert result.status is JobStatus.PUBLISHED and result.platform_post_id == POST_ID and not result.simulated
    steps = [(r.method, r.url.host, r.url.path) for r in fake.requests]
    assert steps == [
        ("GET", "api.linkedin.com", "/v2/userinfo"),
        ("POST", "api.linkedin.com", "/rest/images"),
        ("PUT", "www.linkedin.com", "/dms-uploads/C4E10AQFoyyAjHPMQuQ/uploaded-image/0"),
        ("GET", "api.linkedin.com", "/rest/images/urn:li:image:C4E10AQFoyyAjHPMQuQ"),
        ("POST", "api.linkedin.com", "/rest/posts"),
    ]


def test_requests_carry_the_documented_headers_and_body():
    fake = FakeLinkedIn()
    publish(fake)

    initialize = fake.calls("POST", "/rest/images")[0]
    assert initialize.url.params["action"] == "initializeUpload"
    assert json.loads(initialize.content) == {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
    upload = fake.calls("PUT", "/")[0]
    assert upload.content == JPEG  # Exactly the bytes handed over.
    assert upload.headers["Authorization"] == f"Bearer {TOKEN}"
    [post] = fake.calls("POST", "/rest/posts")
    assert post.headers["Linkedin-Version"] == LINKEDIN_VERSION == "202610"
    assert post.headers["X-Restli-Protocol-Version"] == "2.0.0"
    assert post.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(post.content) == {
        "author": "urn:li:person:abc123",
        "commentary": "Launch \\(beta\\) #news",  # Escaped; the hashtag kept.
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"media": {"id": IMAGE_URN}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def test_waits_while_linkedin_processes_the_image():
    fake = FakeLinkedIn(image_statuses=("WAITING_UPLOAD", "PROCESSING", "PROCESSING", "AVAILABLE"))

    result, sleeps = publish(fake, poll_interval_s=2.0)

    assert result.status is JobStatus.PUBLISHED
    assert len(fake.calls("GET", "/rest/images/")) == 4 and sleeps == [2.0, 2.0, 2.0]


# Checks before anything is sent


def test_caption_over_the_limit_sends_nothing():
    fake = FakeLinkedIn()

    result, _ = publish(fake, make_post(caption="x" * (MAX_CAPTION_LENGTH + 1)))

    assert result.status is JobStatus.FAILED and "LinkedIn allows at most 3000" in result.error
    assert fake.requests == []


def test_image_over_the_pixel_limit_sends_nothing():
    fake = FakeLinkedIn()
    huge = bytearray((IMAGES / "image-6x3.png").read_bytes())
    huge[16:24] = (7000).to_bytes(4, "big") + (7000).to_bytes(4, "big")

    result, _ = publish(fake, make_post(media=bytes(huge)))

    assert result.status is JobStatus.FAILED and "LinkedIn accepts less than 36,152,320" in result.error
    assert fake.requests == []


# Failures before the post: always FAILED, POST /rest/posts never sent


@pytest.mark.parametrize(("fake", "message"), [
    (FakeLinkedIn(userinfo=httpx.Response(401)), "LinkedIn rejected the access token (401)"),
    (FakeLinkedIn(userinfo=httpx.ConnectError), "reading the LinkedIn member failed (ConnectError)"),
    (FakeLinkedIn(userinfo=httpx.Response(200, json={"name": "no id"})), "did not return a member ID"),
    (FakeLinkedIn(initialize=httpx.Response(500)), "registering the image upload: LinkedIn answered 500"),
    (FakeLinkedIn(initialize=httpx.ReadTimeout), "registering the image upload failed (ReadTimeout)"),
    (FakeLinkedIn(initialize=httpx.Response(200, json={"value": {}})), "did not return an image ID"),
    (FakeLinkedIn(upload=httpx.Response(500)), "uploading the image: LinkedIn answered 500"),
    (FakeLinkedIn(upload=httpx.WriteTimeout), "uploading the image failed (WriteTimeout)"),
    (FakeLinkedIn(upload_url="https://evil.example.com/upload"), "not a LinkedIn HTTPS address"),
    (FakeLinkedIn(upload_url="http://www.linkedin.com/upload"), "not a LinkedIn HTTPS address"),
    (FakeLinkedIn(image=httpx.Response(403)), "does not let this token read the image's processing status"),
    (FakeLinkedIn(image_statuses=("PROCESSING_FAILED",)), "could not process the image"),
    (FakeLinkedIn(image_statuses=("PROCESSING",)), "still processing the image after"),
    (FakeLinkedIn(image=httpx.Response(429)), "rate limit was reached (429)"),
])
def test_failures_before_the_post_are_failed_and_never_create_one(fake, message):
    result, _ = publish(fake, poll_attempts=3)

    assert result.status is JobStatus.FAILED
    assert message in result.error and result.error.endswith("nothing was posted")
    assert fake.calls("POST", "/rest/posts") == []


def test_a_non_linkedin_upload_address_never_receives_the_token():
    fake = FakeLinkedIn(upload_url="https://evil.example.com/upload")
    publish(fake)
    assert all(r.url.host.endswith("linkedin.com") for r in fake.requests)


# POST /rest/posts: the outcome rules


@pytest.mark.parametrize(("behaviour", "expected"), [
    (httpx.ConnectError, "could not connect to LinkedIn (ConnectError); nothing was sent"),
    (httpx.ConnectTimeout, "could not connect to LinkedIn (ConnectTimeout); nothing was sent"),
    (httpx.Response(400, json={"code": "FIELD_LENGTH_TOO_LONG", "message": "x"}),
     "LinkedIn refused the post: LinkedIn answered 400 FIELD_LENGTH_TOO_LONG; nothing was posted"),
    (httpx.Response(401), "LinkedIn rejected the access token (401): it has expired or is invalid. "
                          "Renew LINKEDIN_ACCESS_TOKEN in backend\\.env; nothing was posted"),
    (httpx.Response(403, json={"code": "ACCESS_DENIED"}), "LinkedIn answered 403 ACCESS_DENIED; nothing was posted"),
    (httpx.Response(404), "LinkedIn answered 404; nothing was posted"),
    (httpx.Response(422), "LinkedIn answered 422; nothing was posted"),
    (httpx.Response(429), "LinkedIn's rate limit was reached (429); try again later; nothing was posted"),
])
def test_definite_refusals_are_failed(behaviour, expected):
    fake = FakeLinkedIn(post=behaviour)

    result, _ = publish(fake)

    assert result.status is JobStatus.FAILED and result.error.endswith(expected)
    assert len(fake.calls("POST", "/rest/posts")) == 1


@pytest.mark.parametrize(("behaviour", "expected"), [
    (httpx.ReadTimeout, "the connection failed after the post was sent (ReadTimeout)"),
    (httpx.WriteTimeout, "the connection failed after the post was sent (WriteTimeout)"),
    (httpx.RemoteProtocolError, "the connection failed after the post was sent (RemoteProtocolError)"),
    (httpx.ReadError, "the connection failed after the post was sent (ReadError)"),  # Dropped connection.
    (httpx.WriteError, "the connection failed after the post was sent (WriteError)"),
    (httpx.Response(500), "LinkedIn answered 500; the post may exist"),
    (httpx.Response(502), "LinkedIn answered 502; the post may exist"),
    (httpx.Response(503), "LinkedIn answered 503; the post may exist"),
    (httpx.Response(409, json={"code": "CONFLICT"}), "LinkedIn answered 409 CONFLICT; the post may exist"),
    (httpx.Response(201), "LinkedIn answered 201 without a post ID"),
    (httpx.Response(201, headers={"x-restli-id": "  "}), "LinkedIn answered 201 without a post ID"),
    (httpx.Response(200, json={}), "LinkedIn answered 200; the post may exist"),  # Undocumented status.
])
def test_uncertain_outcomes_raise_and_are_never_retried(behaviour, expected):
    fake = FakeLinkedIn(post=behaviour)

    with pytest.raises(PublishOutcomeUnknown, match="check LinkedIn before re-posting") as raised:
        publish(fake)

    assert expected in str(raised.value)
    assert len(fake.calls("POST", "/rest/posts")) == 1  # Sent exactly once.


def test_the_default_transport_has_retries_switched_off():
    client = LinkedInClient(TOKEN)
    try:
        transport = client._client._transport
        assert isinstance(transport, httpx.HTTPTransport)
        assert transport._pool._retries == 0
        assert client._client.follow_redirects is False
    finally:
        client.close()


def test_linkedin_error_messages_are_never_kept():
    caption = "Secret launch details (embargoed)"
    fake = FakeLinkedIn(post=httpx.Response(422, json={"code": "INVALID_VALUE_FOR_FIELD",
                                                       "message": f"commentary cannot be set to {caption}"}))

    result, _ = publish(fake, make_post(caption=caption))

    assert "INVALID_VALUE_FOR_FIELD" in result.error and "embargoed" not in result.error


# The token


def test_the_token_never_appears_in_results_errors_or_reprs():
    texts = []
    for post in (httpx.Response(401), httpx.Response(500), httpx.ReadTimeout, httpx.ConnectError):
        fake = FakeLinkedIn(post=post)
        try:
            result, _ = publish(fake)
            texts.append(repr(result))
        except PublishOutcomeUnknown as error:
            texts.append(repr(error))
            texts.append(str(error.__cause__))
    with LinkedInClient(TOKEN) as client:
        texts += [repr(client), repr(LinkedInPublisher(client)), str(vars(LinkedInPublisher(client)))]

    assert all(TOKEN not in text for text in texts)
