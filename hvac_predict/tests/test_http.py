import httpx
import pytest

from hvac_predict.errors import ConfigError, ServiceError
from hvac_predict.http import JsonApi


def api(handler, token: str | None = "abc.DEF-123_~+/=") -> JsonApi:
    transport = httpx.MockTransport(handler)
    return JsonApi("Home Assistant", "http://ha.test", token, timeout=5, transport=transport)


def test_a_request_carries_the_token_and_returns_the_decoded_body():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"answer": 42})

    body = api(handler).get("/api/config", {"detail": "full"})

    (request,) = seen
    assert body == {"answer": 42}
    assert str(request.url) == "http://ha.test/api/config?detail=full"
    assert request.headers["Authorization"] == "Bearer abc.DEF-123_~+/="


def test_a_post_sends_its_payload_as_json():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    api(handler).post("/api/services/x/y", {"entity_id": "x.y"}, params={"return_response": ""})

    (request,) = seen
    assert request.method == "POST"
    assert request.content == b'{"entity_id":"x.y"}'
    assert "return_response" in request.url.params


def test_no_token_means_no_authorization_header():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    api(handler, token=None).get("/x")

    assert "Authorization" not in seen[0].headers


@pytest.mark.parametrize("token", ["abc def", "abc\rdef", "abc\ndef", "abc\x00", "tøken", ""])
def test_a_token_with_characters_no_token_has_is_refused_without_being_repeated(token):
    # HVP-013
    with pytest.raises(ConfigError) as refused:
        api(lambda request: httpx.Response(200, json={}), token=token)

    assert "the token for Home Assistant contains characters a token cannot have" in str(
        refused.value
    )
    assert "abc" not in str(refused.value)


def test_an_error_status_is_reported_with_what_the_server_said():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"status": "error", "error": "cannot parse the query"})

    with pytest.raises(ServiceError) as failed:
        api(handler).get("/api/v1/query_range")

    message = str(failed.value)
    assert message.startswith(
        "Home Assistant answered GET /api/v1/query_range with 422 Unprocessable Entity: "
    )
    assert "cannot parse the query" in message


def test_what_the_server_said_is_quoted_briefly_and_as_plain_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="\x1b[31mboom\n" + "x" * 5000)

    with pytest.raises(ServiceError) as failed:
        api(handler).get("/x")

    assert "\x1b" not in str(failed.value)
    assert "\n" not in str(failed.value)
    assert len(str(failed.value)) < 400


def test_a_redirect_is_reported_and_not_followed():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://elsewhere.test/x"})

    with pytest.raises(ServiceError, match="302 Found"):
        api(handler).get("/x")

    assert len(requests) == 1


def test_a_server_that_cannot_be_reached_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(
        ServiceError, match="cannot reach Home Assistant: ConnectError: connection refused"
    ):
        api(handler).get("/x")


def test_a_body_that_is_not_json_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Sign in</html>")

    with pytest.raises(ServiceError, match="answered GET /x with something other than JSON"):
        api(handler).get("/x")
