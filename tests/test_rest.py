import logging
import warnings
from unittest.mock import Mock

import orjson
import pytest
import requests

from fastkalshi.rest import rest


def response(status, payload=None, *, text="", reason="", headers=None):
    result = Mock()
    result.status_code = status
    result.content = b"" if payload is None else orjson.dumps(payload)
    result.text = text
    result.reason = reason
    result.headers = headers or {}
    return result


def test_request_accepts_empty_success(monkeypatch):
    monkeypatch.setattr(
        rest.WRITE_SESSION,
        "request",
        Mock(return_value=response(204)),
    )

    assert rest.request("DELETE", "https://example.test/orders") is None


@pytest.mark.parametrize(
    ("method", "content"),
    [
        ("HEAD", b""),
        ("HEAD", b"ignored"),
        ("OPTIONS", b""),
    ],
)
def test_bodyless_read_responses_are_valid(monkeypatch, method, content):
    request = Mock(return_value=Mock(status_code=200, content=content))
    monkeypatch.setattr(rest.SESSION, "request", request)

    assert rest.request(method, "https://example.test/resource") is None


@pytest.mark.parametrize("content", [b"", b"{", b"null", b"[]"])
def test_mutation_success_requires_valid_json(monkeypatch, content):
    response = Mock(status_code=201, content=content)
    monkeypatch.setattr(
        rest.WRITE_SESSION,
        "request",
        Mock(return_value=response),
    )

    with pytest.raises(rest.KalshiResponseError) as caught:
        rest.request("POST", "https://example.test/orders", body={})

    assert caught.value.outcome_unknown is True
    assert caught.value.response is response
    assert "did not contain a JSON object" in str(caught.value)


def test_request_raises_structured_api_error(monkeypatch):
    monkeypatch.setattr(
        rest.WRITE_SESSION,
        "request",
        Mock(
            return_value=response(
                400,
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Bad request",
                        "details": {"field": "price"},
                    }
                },
            )
        ),
    )

    with pytest.raises(rest.KalshiAPIError) as caught:
        rest.request("POST", "https://example.test/orders", body={})

    assert caught.value.status_code == 400
    assert caught.value.outcome_unknown is False
    assert caught.value.code == "invalid_request"
    assert caught.value.details == {"field": "price"}
    assert caught.value.payload["error"]["message"] == "Bad request"


def test_rate_limit_error_exposes_response_metadata(monkeypatch):
    response_value = response(
        429,
        {"error": {"message": "too many requests"}},
        headers={
            "Retry-After": "0.25",
            "X-RateLimit-Limit": "300",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1.5",
            "X-RateLimit-Bucket": "write",
            "X-Request-ID": "request-123",
        },
    )
    monkeypatch.setattr(
        rest.WRITE_SESSION,
        "request",
        Mock(return_value=response_value),
    )

    with pytest.raises(rest.KalshiRateLimitError) as caught:
        rest.request("POST", "https://example.test/orders", body={})

    assert caught.value.retry_after_seconds == 0.25
    assert caught.value.limit == 300
    assert caught.value.remaining == 0
    assert caught.value.reset == 1.5
    assert caught.value.bucket == "write"
    assert caught.value.request_id == "request-123"
    assert caught.value.response is response_value


def test_rate_limit_error_parses_retry_after_http_date(monkeypatch):
    monkeypatch.setattr(
        rest.WRITE_SESSION,
        "request",
        Mock(
            return_value=response(
                429,
                {"error": {"message": "too many requests"}},
                headers={
                    "Date": "Sun, 20 Sep 2026 20:00:00 GMT",
                    "Retry-After": "Sun, 20 Sep 2026 20:00:05 GMT",
                },
            )
        ),
    )

    with pytest.raises(rest.KalshiRateLimitError) as caught:
        rest.request("POST", "https://example.test/orders", body={})

    assert caught.value.retry_after_seconds == 5.0


def test_request_observer_receives_success_and_error(monkeypatch):
    events = []
    rest.set_request_observer(events.append)
    monkeypatch.setattr(
        rest.SESSION,
        "request",
        Mock(
            side_effect=[
                response(200, {}, headers={"X-Request-ID": "success"}),
                response(429, {"error": {"message": "too many requests"}}),
            ]
        ),
    )
    try:
        assert rest.request("GET", "https://example.test/markets") == {}
        with pytest.raises(rest.KalshiRateLimitError):
            rest.request("GET", "https://example.test/markets")
    finally:
        rest.set_request_observer(None)

    assert [event.status_code for event in events] == [200, 429]
    assert events[0].headers["X-Request-ID"] == "success"
    assert events[0].error is None
    assert isinstance(events[1].error, rest.KalshiRateLimitError)
    assert all(event.elapsed_seconds >= 0 for event in events)


def test_request_observer_receives_transport_error(monkeypatch):
    events = []
    rest.set_request_observer(events.append)
    monkeypatch.setattr(
        rest.SESSION,
        "request",
        Mock(side_effect=requests.ConnectionError("connection failed")),
    )
    try:
        with pytest.raises(rest.KalshiTransportError) as caught:
            rest.request("GET", "https://example.test/markets")
    finally:
        rest.set_request_observer(None)

    assert len(events) == 1
    assert events[0].status_code is None
    assert events[0].headers == {}
    assert events[0].error is caught.value
    assert events[0].elapsed_seconds >= 0


def test_request_observer_errors_are_logged_without_breaking_requests(
    monkeypatch,
    caplog,
):
    rest.set_request_observer(
        lambda _event: (_ for _ in ()).throw(RuntimeError("observer failed"))
    )
    monkeypatch.setattr(
        rest.SESSION,
        "request",
        Mock(return_value=response(200, {})),
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with caplog.at_level(logging.ERROR, logger=rest.__name__):
                assert rest.request("GET", "https://example.test/markets") == {}
    finally:
        rest.set_request_observer(None)

    assert "fastkalshi request observer failed" in caplog.text
    assert "observer failed" in caplog.text


def test_request_observer_receives_invalid_success_as_error(monkeypatch):
    events = []
    rest.set_request_observer(events.append)
    monkeypatch.setattr(
        rest.WRITE_SESSION,
        "request",
        Mock(return_value=response(201)),
    )
    try:
        with pytest.raises(rest.KalshiResponseError):
            rest.request("POST", "https://example.test/orders", body={})
    finally:
        rest.set_request_observer(None)

    assert len(events) == 1
    assert events[0].status_code == 201
    assert isinstance(events[0].error, rest.KalshiResponseError)


@pytest.mark.parametrize(
    ("method", "status", "outcome_unknown"),
    [
        ("GET", 500, False),
        ("POST", 500, True),
        ("DELETE", 408, True),
        ("POST", 429, False),
    ],
)
def test_api_errors_mark_only_ambiguous_mutations(
    monkeypatch,
    method,
    status,
    outcome_unknown,
):
    response_value = response(
        status,
        {"error": {"message": "request failed"}},
    )
    request = Mock(return_value=response_value)
    monkeypatch.setattr(rest.SESSION, "request", request)
    monkeypatch.setattr(rest.WRITE_SESSION, "request", request)

    with pytest.raises(rest.KalshiAPIError) as caught:
        rest.request(method, "https://example.test/resource")

    assert caught.value.outcome_unknown is outcome_unknown


def test_request_does_not_retry_failures(monkeypatch):
    request = Mock(
        return_value=response(
            503,
            {"error": {"message": "unavailable"}},
        )
    )
    monkeypatch.setattr(rest.SESSION, "request", request)

    with pytest.raises(rest.KalshiAPIError):
        rest.request("GET", "https://example.test/markets")
    assert request.call_count == 1


def test_mutations_use_a_separate_session(monkeypatch):
    read_request = Mock(return_value=response(200, {}))
    write_request = Mock(return_value=response(201, {}))
    monkeypatch.setattr(rest.SESSION, "request", read_request)
    monkeypatch.setattr(rest.WRITE_SESSION, "request", write_request)

    rest.request("GET", "https://example.test/markets")
    rest.request("POST", "https://example.test/orders", body={})

    assert read_request.call_count == 1
    assert write_request.call_count == 1


@pytest.mark.parametrize(
    ("method", "outcome_unknown"),
    [("GET", False), ("POST", True), ("DELETE", True)],
)
def test_transport_errors_mark_uncertain_mutations(
    monkeypatch,
    method,
    outcome_unknown,
):
    request = Mock(side_effect=requests.Timeout("timed out"))
    monkeypatch.setattr(rest.SESSION, "request", request)
    monkeypatch.setattr(rest.WRITE_SESSION, "request", request)

    with pytest.raises(rest.KalshiTransportError) as caught:
        rest.request(method, "https://example.test/orders")

    assert caught.value.outcome_unknown is outcome_unknown
    assert request.call_count == 1


def test_boolean_query_values_are_lowercase(monkeypatch):
    request = Mock(return_value=response(200, {}))
    monkeypatch.setattr(rest.SESSION, "request", request)

    rest.get("https://example.test/events", with_nested_markets=True)

    assert request.call_args.kwargs["params"] == {"with_nested_markets": "true"}
    assert request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("caller", [rest.get, rest.post, rest.put, rest.delete])
@pytest.mark.parametrize("timeout", [1.5, (0.5, 1.0)])
def test_timeout_is_applied_and_not_sent_as_a_query_parameter(
    monkeypatch,
    caller,
    timeout,
):
    request = Mock(return_value=response(200, {}))
    monkeypatch.setattr(rest.SESSION, "request", request)
    monkeypatch.setattr(rest.WRITE_SESSION, "request", request)

    caller("https://example.test/orders", timeout=timeout)

    assert request.call_args.kwargs["timeout"] == timeout
    assert request.call_args.kwargs["params"] is None
