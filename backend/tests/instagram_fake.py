"""A fake Instagram Graph API for tests, on ``httpx.MockTransport``: no network is ever reached.

Each operation answers with a default success unless a test sets ``answers[op]`` to:
- an ``httpx.Response`` (returned every time),
- an exception instance (raised every time, e.g. ``httpx.ReadTimeout("lost")``),
- a list of those (used in turn; the last one repeats), or
- a callable taking the request and returning one of those.

Every request is kept in ``requests`` and its operation name in ``events`` (tests can append their own
events, e.g. the recorder's, to prove the order of calls).
"""

import httpx

TOKEN = "IG-TEST-TOKEN-must-never-leak-7f3a"
USER_ID = "17841400000000001"
CONTAINER_ID = "17889455560051444"
MEDIA_ID = "17920238422030506"


def json_response(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


def graph_error(status: int, code: int, subcode: int | None = None, message: str = "refused") -> httpx.Response:
    error: dict = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "A6LJylpZRKw2"}
    if subcode is not None:
        error["error_subcode"] = subcode
    return json_response(status, {"error": error})


DEFAULTS = {
    "limit": json_response(200, {"data": [{"quota_usage": 2, "config": {"quota_total": 50, "quota_duration": 86400}}]}),
    "create": json_response(200, {"id": CONTAINER_ID}),
    "status": json_response(200, {"status_code": "FINISHED", "id": CONTAINER_ID}),
    "publish": json_response(200, {"id": MEDIA_ID}),
    "media": json_response(200, {"data": []}),
}


class FakeGraph:
    def __init__(self) -> None:
        self.answers: dict[str, object] = {}
        self.requests: list[httpx.Request] = []
        self.events: list[str] = []
        self._used: dict[str, int] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def count(self, operation: str) -> int:
        return self.events.count(operation)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        operation = _operation(request)
        self.requests.append(request)
        self.events.append(operation)
        answer = self.answers.get(operation, DEFAULTS.get(operation))
        if isinstance(answer, list):
            index = min(self._used.get(operation, 0), len(answer) - 1)
            self._used[operation] = self._used.get(operation, 0) + 1
            answer = answer[index]
        if callable(answer) and not isinstance(answer, httpx.Response):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer
        if not isinstance(answer, httpx.Response):
            raise AssertionError(f"no answer for {operation}: {request.method} {request.url.path}")
        return answer


def _operation(request: httpx.Request) -> str:
    assert request.url.host == "graph.instagram.com", request.url
    parts = request.url.path.strip("/").split("/")  # ["v27.0", <id>, <edge>?]
    assert parts[0] == "v27.0", request.url.path
    if request.method == "GET" and parts[1:] == [USER_ID, "content_publishing_limit"]:
        return "limit"
    if request.method == "POST" and parts[1:] == [USER_ID, "media"]:
        return "create"
    if request.method == "POST" and parts[1:] == [USER_ID, "media_publish"]:
        return "publish"
    if request.method == "GET" and parts[1:] == [USER_ID, "media"]:
        return "media"
    if request.method == "GET" and len(parts) == 2 and parts[1].isdigit():
        return "status"
    raise AssertionError(f"unexpected request: {request.method} {request.url.path}")
