"""JSON over HTTP, with every failure reported as a ServiceError."""

import re
from typing import Any

import httpx

from .errors import ConfigError, ServiceError

# The characters RFC 6750 allows in a bearer token. Anything else, such as the
# carriage return a file saved on Windows leaves behind, would make httpx
# refuse the header and quote the whole token in its error message.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]+")
_MAX_QUOTED_BODY = 200


class JsonApi:
    """One service's HTTP API. Redirects are not followed, so a token stays with its service."""

    def __init__(
        self,
        service: str,
        url: str,
        token: str | None,
        *,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if token is not None and not _TOKEN.fullmatch(token):
            raise ConfigError(f"the token for {service} contains characters a token cannot have")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._service = service
        self._client = httpx.Client(
            base_url=url, headers=headers, timeout=timeout, transport=transport
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: Any, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, params=params, json=payload)

    def _request(self, method: str, path: str, **options: Any) -> Any:
        try:
            response = self._client.request(method, path, **options)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            # The body usually says what was wrong with the request, such as a bad query.
            status = f"{error.response.status_code} {error.response.reason_phrase}"
            said = error.response.text.strip()[:_MAX_QUOTED_BODY]
            raise ServiceError(
                f"{self._service} answered {method} {path} with {status}"
                + (f": {said!r}" if said else "")
            ) from error
        except httpx.HTTPError as error:
            raise ServiceError(
                f"cannot reach {self._service}: {type(error).__name__}: {error}"
            ) from error
        except ValueError as error:
            raise ServiceError(
                f"{self._service} answered {method} {path} with something other than JSON"
            ) from error
