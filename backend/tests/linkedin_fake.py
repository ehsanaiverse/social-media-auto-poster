"""A fake LinkedIn for tests: answers every call the publisher makes, through httpx.MockTransport.

Shared by the unit and integration tests. No network is ever used.
"""

from pathlib import Path

import httpx

TOKEN = "TEST-TOKEN-must-never-leak-7f3a"
IMAGE_URN = "urn:li:image:C4E10AQFoyyAjHPMQuQ"
UPLOAD_URL = "https://www.linkedin.com/dms-uploads/C4E10AQFoyyAjHPMQuQ/uploaded-image/0?ca=vector&ut=abc"
POST_ID = "urn:li:share:6844785523593134080"
IMAGES = Path(__file__).resolve().parent / "fixtures" / "images"
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
