import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from market.execution.robinhood.auth import RobinhoodReadCredentials, verify_signature
from market.execution.robinhood.read_client import (
    MAX_ATTEMPTS,
    RobinhoodReadAuthorizationError,
    RobinhoodReadError,
    RobinhoodReadRateLimitError,
    RobinhoodReadSchemaError,
    RobinhoodReadTransportError,
    RobinhoodV2ReadClient,
)

FIXTURE = Path(__file__).parent / "fixtures" / "robinhood" / "v2_cost_snapshot.json"
FIXED_NOW = datetime(2026, 8, 17, 12, 0, 5, tzinfo=UTC)
API_KEY = "rh-api-6148effc-c0b1-486c-8940-a1d099456be6"
PRIVATE_KEY = "xQnTJVeQLmw1/Mg2YimEViSpw/SdJcgNXZ5kQkAXNPU="
PUBLIC_KEY = "jPItx4TLjcnSUnmnXQQyAKL4eJj3+oWNNMmmm2vATqk="


def _credentials() -> RobinhoodReadCredentials:
    return RobinhoodReadCredentials(
        credential_label="g3-test",
        api_key=API_KEY,
        private_key_base64=PRIVATE_KEY,
        public_key_base64=PUBLIC_KEY,
        public_key_fingerprint="sha256:test",
    )


def _fixture() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _signed_path(request: httpx.Request) -> str:
    return request.url.raw_path.decode("ascii")


def _assert_valid_get_signature(request: httpx.Request) -> None:
    assert request.method == "GET"
    assert request.content == b""
    assert request.url.scheme == "https"
    assert request.url.host == "trading.robinhood.com"
    assert request.headers["x-api-key"] == API_KEY
    timestamp = int(request.headers["x-timestamp"])
    assert timestamp == int(FIXED_NOW.timestamp())
    assert verify_signature(
        public_key_base64=PUBLIC_KEY,
        signature_base64=request.headers["x-signature"],
        api_key=API_KEY,
        timestamp=timestamp,
        path=_signed_path(request),
        method="GET",
    )


def test_client_calls_only_four_exact_get_resources_with_signed_query_paths() -> None:
    fixture = _fixture()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_valid_get_signature(request)
        path = _signed_path(request)
        seen.append(path)
        if path == "/api/v2/crypto/trading/accounts/":
            payload = fixture["accounts_response"]
        elif path == "/api/v2/crypto/trading/trading_pairs/?symbol=BTC-USD":
            payload = fixture["trading_pairs_response"]
        elif path == "/api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD":
            payload = fixture["best_bid_ask_response"]
        elif path == (
            "/api/v2/crypto/trading/estimated_price/?symbol=BTC-USD&side=both&quantity=0.001"
        ):
            payload = fixture["estimated_price_response"]
        else:
            raise AssertionError(f"unexpected path {path}")
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(
        _credentials(),
        http_client=http,
        now=lambda: FIXED_NOW,
    )
    accounts = client.get_accounts()
    pair = client.get_trading_pair("BTC-USD")
    best = client.get_best_bid_ask("BTC-USD")
    estimates = client.get_estimated_price("BTC-USD", (Decimal("0.001"),))

    assert accounts.response.results[0].account_number == "synthetic-account-must-be-redacted"
    assert pair.response.results[0].symbol == "BTC-USD"
    assert best.response.results[0].bid == Decimal("62990")
    assert estimates.response.results[0].quantity == Decimal("0.001")
    assert all(item.received_at == FIXED_NOW for item in (accounts, pair, best, estimates))
    assert seen == [
        "/api/v2/crypto/trading/accounts/",
        "/api/v2/crypto/trading/trading_pairs/?symbol=BTC-USD",
        "/api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD",
        "/api/v2/crypto/trading/estimated_price/?symbol=BTC-USD&side=both&quantity=0.001",
    ]
    assert not hasattr(client, "post")
    assert not hasattr(client, "place_order")
    assert not hasattr(client, "request")
    http.close()


def test_multi_quantity_query_preserves_frozen_order_and_literal_commas() -> None:
    observed_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_path
        observed_path = _signed_path(request)
        return httpx.Response(200, json={"results": []})

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(_credentials(), http_client=http, now=lambda: FIXED_NOW)
    with pytest.raises(RobinhoodReadSchemaError):
        client.get_estimated_price(
            "BTC-USD",
            (
                Decimal("0.00025"),
                Decimal("0.0005"),
                Decimal("0.001"),
                Decimal("0.002"),
            ),
        )

    assert observed_path.endswith("quantity=0.00025,0.0005,0.001,0.002")
    http.close()


def test_unauthorized_symbol_and_quantity_shape_fail_before_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(_credentials(), http_client=http, now=lambda: FIXED_NOW)
    with pytest.raises(RobinhoodReadError, match="only_btc_usd"):
        client.get_best_bid_ask("ETH-USD")
    with pytest.raises(RobinhoodReadError, match="unique_and_sorted"):
        client.get_estimated_price("BTC-USD", (Decimal("0.001"), Decimal("0.001")))
    with pytest.raises(RobinhoodReadError, match="one_to_ten"):
        client.get_estimated_price("BTC-USD", ())
    assert calls == 0
    http.close()


def test_401_and_403_are_not_retried_and_errors_do_not_echo_credentials() -> None:
    for status in (401, 403):
        calls = 0

        def handler(request: httpx.Request, response_status: int = status) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(response_status, text=f"secret {API_KEY}")

        http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        client = RobinhoodV2ReadClient(_credentials(), http_client=http, now=lambda: FIXED_NOW)
        with pytest.raises(RobinhoodReadAuthorizationError) as raised:
            client.get_accounts()
        assert calls == 1
        assert API_KEY not in str(raised.value)
        assert "secret" not in str(raised.value)
        http.close()


def test_rate_limit_retries_are_bounded_and_honor_capped_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "99"})

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(
        _credentials(),
        http_client=http,
        now=lambda: FIXED_NOW,
        sleep=sleeps.append,
    )
    with pytest.raises(RobinhoodReadRateLimitError, match="attempts_exhausted"):
        client.get_accounts()

    assert calls == MAX_ATTEMPTS
    assert sleeps == [5.0, 5.0]
    http.close()


def test_transport_errors_retry_only_to_fixed_budget() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("synthetic timeout", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(
        _credentials(),
        http_client=http,
        now=lambda: FIXED_NOW,
        sleep=sleeps.append,
        jitter=lambda low, high: 0.0,
    )
    with pytest.raises(RobinhoodReadTransportError, match="attempts_exhausted"):
        client.get_accounts()

    assert calls == MAX_ATTEMPTS
    assert sleeps == [0.25, 0.5]
    http.close()


def test_schema_error_omits_account_identifier_and_response_values() -> None:
    sensitive_account = "must-never-appear-in-error"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "next": None,
                "previous": None,
                "results": [{"account_number": sensitive_account, "status": {"bad": "value"}}],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(_credentials(), http_client=http, now=lambda: FIXED_NOW)
    with pytest.raises(RobinhoodReadSchemaError) as raised:
        client.get_accounts()

    message = str(raised.value)
    assert sensitive_account not in message
    assert "bad" not in message
    assert "account_number" not in message
    assert "schema_invalid" in message
    assert raised.value.__cause__ is None
    http.close()


def test_redirect_and_oversized_responses_fail_closed() -> None:
    responses = iter(
        (
            httpx.Response(302, headers={"Location": "https://evil.example/collect"}),
            httpx.Response(200, content=b"x" * 1_000_001),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = RobinhoodV2ReadClient(_credentials(), http_client=http, now=lambda: FIXED_NOW)
    with pytest.raises(RobinhoodReadError, match="redirect_response_forbidden"):
        client.get_accounts()
    with pytest.raises(RobinhoodReadSchemaError, match="response_too_large"):
        client.get_accounts()
    http.close()
