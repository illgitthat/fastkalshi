import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import orjson
import requests

SESSION = requests.Session()
WRITE_SESSION = requests.Session()
DEFAULT_TIMEOUT = 10.0
type RequestTimeout = float | tuple[float, float]


@dataclass(frozen=True, slots=True)
class KalshiRequestEvent:
    method: str
    url: str
    status_code: int | None
    elapsed_seconds: float
    headers: Mapping[str, str]
    error: Exception | None = None


type RequestObserver = Callable[[KalshiRequestEvent], None]
_REQUEST_OBSERVER: RequestObserver | None = None


def set_request_observer(observer: RequestObserver | None) -> None:
    global _REQUEST_OBSERVER
    _REQUEST_OBSERVER = observer


def _notify_request_observer(event: KalshiRequestEvent) -> None:
    observer = _REQUEST_OBSERVER
    if observer is None:
        return
    try:
        observer(event)
    except Exception as error:  # noqa: BLE001 - observer failures cannot break requests
        warnings.warn(
            f"fastkalshi request observer failed: {error}",
            RuntimeWarning,
            stacklevel=2,
        )
        return


def _notify_response(
    *,
    method: str,
    url: str,
    started_at: float,
    response: requests.Response,
    error: Exception | None = None,
) -> None:
    _notify_request_observer(
        KalshiRequestEvent(
            method=method,
            url=url,
            status_code=response.status_code,
            elapsed_seconds=time.perf_counter() - started_at,
            headers=_response_headers(response),
            error=error,
        )
    )


def _response_headers(response: requests.Response | None) -> dict[str, str]:
    if response is None:
        return {}
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def _header_value(headers: Mapping[str, str], *names: str) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for name in names:
        value = normalized.get(name.lower())
        if value:
            return value
    return None


def _header_float(headers: Mapping[str, str], *names: str) -> float | None:
    value = _header_value(headers, *names)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _header_int(headers: Mapping[str, str], *names: str) -> int | None:
    value = _header_value(headers, *names)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class KalshiAPIError(requests.HTTPError):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        method: str,
        url: str,
        code: str | None = None,
        details: Any = None,
        payload: Any = None,
        response: requests.Response | None = None,
    ):
        super().__init__(
            f"Kalshi API error {status_code}: {message}", response=response
        )
        self.status_code = status_code
        self.method = method
        self.url = url
        self.code = code
        self.message = message
        self.details = details
        self.payload = payload
        self.headers = _response_headers(response)
        self.request_id = _header_value(
            self.headers,
            "X-Request-ID",
            "Kalshi-Request-ID",
        )
        self.outcome_unknown = method not in {"GET", "HEAD", "OPTIONS"} and (
            status_code == 408 or status_code >= 500
        )


class KalshiRateLimitError(KalshiAPIError):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        method: str,
        url: str,
        code: str | None = None,
        details: Any = None,
        payload: Any = None,
        response: requests.Response | None = None,
    ):
        super().__init__(
            status_code,
            message,
            method=method,
            url=url,
            code=code,
            details=details,
            payload=payload,
            response=response,
        )
        self.retry_after_seconds = _header_float(self.headers, "Retry-After")
        self.limit = _header_int(self.headers, "X-RateLimit-Limit")
        self.remaining = _header_int(self.headers, "X-RateLimit-Remaining")
        self.reset = _header_float(self.headers, "X-RateLimit-Reset")
        self.bucket = _header_value(self.headers, "X-RateLimit-Bucket")


class KalshiTransportError(requests.RequestException):
    def __init__(self, method: str, url: str):
        self.method = method
        self.url = url
        self.outcome_unknown = method not in {"GET", "HEAD", "OPTIONS"}
        super().__init__(f"Kalshi {method} request failed without a response")


class KalshiResponseError(ValueError):
    def __init__(
        self,
        method: str,
        url: str,
        response: requests.Response,
    ):
        self.method = method
        self.url = url
        self.response = response
        self.status_code = response.status_code
        self.outcome_unknown = method not in {"GET", "HEAD", "OPTIONS"}
        super().__init__(
            f"Kalshi {method} response {response.status_code} did not contain a JSON object"
        )


class KalshiResponseContractError(ValueError):
    def __init__(
        self,
        method: str,
        url: str,
        payload: dict,
        message: str,
    ):
        self.method = method
        self.url = url
        self.payload = payload
        self.outcome_unknown = method not in {"GET", "HEAD", "OPTIONS"}
        super().__init__(message)


def drop_none(dictionary: dict):
    return {key: value for key, value in dictionary.items() if value is not None}


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _parse_error(
    response: requests.Response,
    method: str,
    url: str,
) -> KalshiAPIError:
    try:
        payload = orjson.loads(response.content)
    except orjson.JSONDecodeError:
        payload = None

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    error_type = KalshiRateLimitError if response.status_code == 429 else KalshiAPIError
    return error_type(
        response.status_code,
        error.get("message") or response.text or response.reason,
        method=method,
        url=url,
        code=error.get("code"),
        details=error.get("details"),
        payload=payload,
        response=response,
    )


def request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    body: dict | list | None = None,
    timeout: RequestTimeout = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
):
    query = {
        key: _query_value(value)
        for key, value in (params or {}).items()
        if value is not None
    }
    method = method.upper()
    active_session = session or (
        SESSION if method in {"GET", "HEAD", "OPTIONS"} else WRITE_SESSION
    )
    started_at = time.perf_counter()
    try:
        response = active_session.request(
            method,
            url,
            params=query or None,
            headers=headers,
            json=body,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        transport_error = KalshiTransportError(method, url)
        _notify_request_observer(
            KalshiRequestEvent(
                method=method,
                url=url,
                status_code=None,
                elapsed_seconds=time.perf_counter() - started_at,
                headers={},
                error=transport_error,
            )
        )
        raise transport_error from error
    if not 200 <= response.status_code < 300:
        api_error = _parse_error(response, method, url)
        _notify_response(
            method=method,
            url=url,
            started_at=started_at,
            response=response,
            error=api_error,
        )
        raise api_error
    if (
        response.status_code == 204
        or method == "HEAD"
        or (method == "OPTIONS" and not response.content)
    ):
        _notify_response(
            method=method,
            url=url,
            started_at=started_at,
            response=response,
        )
        return None
    if not response.content:
        response_error = KalshiResponseError(method, url, response)
        _notify_response(
            method=method,
            url=url,
            started_at=started_at,
            response=response,
            error=response_error,
        )
        raise response_error
    try:
        payload = orjson.loads(response.content)
    except orjson.JSONDecodeError as error:
        response_error = KalshiResponseError(method, url, response)
        _notify_response(
            method=method,
            url=url,
            started_at=started_at,
            response=response,
            error=response_error,
        )
        raise response_error from error
    if not isinstance(payload, dict):
        response_error = KalshiResponseError(method, url, response)
        _notify_response(
            method=method,
            url=url,
            started_at=started_at,
            response=response,
            error=response_error,
        )
        raise response_error
    _notify_response(
        method=method,
        url=url,
        started_at=started_at,
        response=response,
    )
    return payload


def get(
    url,
    headers=None,
    session=None,
    *,
    timeout: RequestTimeout = DEFAULT_TIMEOUT,
    **kwargs,
):
    return request(
        "GET",
        url,
        headers=headers,
        params=kwargs,
        timeout=timeout,
        session=session,
    )


def post(
    url,
    headers=None,
    body=None,
    *,
    timeout: RequestTimeout = DEFAULT_TIMEOUT,
    session=None,
    **kwargs,
):
    return request(
        "POST",
        url,
        headers=headers,
        params=kwargs,
        body=body,
        timeout=timeout,
        session=session,
    )


def put(
    url,
    headers=None,
    body=None,
    *,
    timeout: RequestTimeout = DEFAULT_TIMEOUT,
    session=None,
    **kwargs,
):
    return request(
        "PUT",
        url,
        headers=headers,
        params=kwargs,
        body=body,
        timeout=timeout,
        session=session,
    )


def delete(
    url,
    headers=None,
    body=None,
    *,
    timeout: RequestTimeout = DEFAULT_TIMEOUT,
    session=None,
    **kwargs,
):
    return request(
        "DELETE",
        url,
        headers=headers,
        params=kwargs,
        body=body,
        timeout=timeout,
        session=session,
    )
