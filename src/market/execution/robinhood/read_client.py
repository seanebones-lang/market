"""Strict GET-only client for the G3.2e Robinhood v2 cost study.

Only the four resources described by the G3.2c observation contract are reachable. The public
surface exposes no arbitrary request method, URL, body, order, holding, or transfer operation.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from market.execution.robinhood.auth import RobinhoodReadCredentials, readonly_auth_headers
from market.execution.robinhood.observations import (
    AccountsResponse,
    BestBidAskResponse,
    EstimatedPriceResponse,
    RobinhoodV2ReadEndpoint,
    TradingPairsResponse,
)

ROBINHOOD_TRADING_ORIGIN = "https://trading.robinhood.com"
MAX_RESPONSE_BYTES = 1_000_000
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})
_ALLOWED_QUERY_KEYS: dict[RobinhoodV2ReadEndpoint, frozenset[str]] = {
    RobinhoodV2ReadEndpoint.ACCOUNTS: frozenset(),
    RobinhoodV2ReadEndpoint.TRADING_PAIRS: frozenset({"symbol"}),
    RobinhoodV2ReadEndpoint.BEST_BID_ASK: frozenset({"symbol"}),
    RobinhoodV2ReadEndpoint.ESTIMATED_PRICE: frozenset({"symbol", "side", "quantity"}),
}


class RobinhoodReadError(RuntimeError):
    """A sanitized read-only transport, authorization, or response failure."""


class RobinhoodReadAuthorizationError(RobinhoodReadError):
    """Robinhood rejected the API key, signature, or action scope."""


class RobinhoodReadRateLimitError(RobinhoodReadError):
    """The bounded read retry budget was exhausted on HTTP 429."""


class RobinhoodReadTransportError(RobinhoodReadError):
    """A bounded network request failed without a usable response."""


class RobinhoodReadSchemaError(RobinhoodReadError):
    """A response failed strict schema validation without echoing its data."""


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


@dataclass(frozen=True)
class CapturedRead[ResponseModelT: BaseModel]:
    endpoint: RobinhoodV2ReadEndpoint
    request_path: str
    received_at: datetime
    response: ResponseModelT


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value <= 0:
        raise RobinhoodReadError("quantity_must_be_finite_and_positive")
    return format(value, "f")


def _validate_cost_symbol(symbol: str) -> str:
    if symbol != "BTC-USD":
        raise RobinhoodReadError("only_btc_usd_is_authorized")
    return symbol


def _build_path(
    endpoint: RobinhoodV2ReadEndpoint,
    parameters: Sequence[tuple[str, str]] = (),
) -> str:
    allowed = _ALLOWED_QUERY_KEYS[endpoint]
    keys = [key for key, _ in parameters]
    if any(key not in allowed for key in keys):
        raise RobinhoodReadError("query_key_not_allowlisted")
    if len(keys) != len(set(keys)) and endpoint not in {
        RobinhoodV2ReadEndpoint.TRADING_PAIRS,
        RobinhoodV2ReadEndpoint.BEST_BID_ASK,
    }:
        raise RobinhoodReadError("duplicate_query_key_forbidden")
    query = urlencode(parameters, doseq=True, safe=",")
    return endpoint.value if not query else f"{endpoint.value}?{query}"


def _verify_request_path(endpoint: RobinhoodV2ReadEndpoint, path: str) -> None:
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != endpoint.value:
        raise RobinhoodReadError("request_path_outside_allowlist")
    query_keys = {key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if not query_keys.issubset(_ALLOWED_QUERY_KEYS[endpoint]):
        raise RobinhoodReadError("request_query_outside_allowlist")
    if endpoint is RobinhoodV2ReadEndpoint.ACCOUNTS and parsed.query:
        raise RobinhoodReadError("accounts_query_forbidden")


def _safe_schema_error(endpoint: RobinhoodV2ReadEndpoint, exc: ValidationError) -> str:
    labels: list[str] = []
    for item in exc.errors(include_input=False, include_url=False)[:8]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        labels.append(f"{location}:{item.get('type', 'invalid')}")
    detail = "|".join(labels) if labels else "invalid"
    return f"{endpoint.name.lower()}_schema_invalid:{detail}"


class RobinhoodV2ReadClient:
    """Authenticated, bounded, GET-only access to the cost-study endpoint allowlist."""

    def __init__(
        self,
        credentials: RobinhoodReadCredentials,
        *,
        http_client: httpx.Client | None = None,
        now: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] | None = None,
    ) -> None:
        self._credentials = credentials
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            headers={"User-Agent": "market-g3.2e-readonly-cost-study/1"},
        )
        self._now = now
        self._sleep = sleep
        system_random = random.SystemRandom()
        self._jitter = jitter or system_random.uniform

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None:
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after is not None:
                try:
                    return min(max(float(raw_retry_after), 0.0), 5.0)
                except ValueError:
                    pass
        base = min(0.25 * (2 ** (attempt - 1)), 2.0)
        return base + self._jitter(0.0, min(base / 4, 0.25))

    def _request(
        self,
        endpoint: RobinhoodV2ReadEndpoint,
        path: str,
        response_model: type[ResponseModelT],
    ) -> CapturedRead[ResponseModelT]:
        _verify_request_path(endpoint, path)
        url = f"{ROBINHOOD_TRADING_ORIGIN}{path}"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            signed_at = self._now()
            if signed_at.tzinfo is None or signed_at.utcoffset() is None:
                raise RobinhoodReadError("client_clock_must_be_timezone_aware")
            timestamp = int(signed_at.astimezone(UTC).timestamp())
            headers = readonly_auth_headers(self._credentials, timestamp=timestamp, path=path)
            try:
                response = self._http_client.get(url, headers=headers)
                received_at = self._now().astimezone(UTC)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise RobinhoodReadTransportError("read_transport_attempts_exhausted") from exc
                self._sleep(self._retry_delay(attempt))
                continue

            if response.is_redirect:
                raise RobinhoodReadError("redirect_response_forbidden")
            if response.status_code in {401, 403}:
                raise RobinhoodReadAuthorizationError(
                    f"{endpoint.name.lower()}_authorization_rejected_{response.status_code}"
                )
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt < MAX_ATTEMPTS:
                    self._sleep(self._retry_delay(attempt, response))
                    continue
                if response.status_code == 429:
                    raise RobinhoodReadRateLimitError(
                        f"{endpoint.name.lower()}_rate_limit_attempts_exhausted"
                    )
                raise RobinhoodReadTransportError(
                    f"{endpoint.name.lower()}_server_attempts_exhausted_{response.status_code}"
                )
            if response.status_code != 200:
                raise RobinhoodReadError(
                    f"{endpoint.name.lower()}_unexpected_http_status_{response.status_code}"
                )
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise RobinhoodReadSchemaError(f"{endpoint.name.lower()}_response_too_large")

            try:
                value = json.loads(response.content, parse_float=Decimal)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise RobinhoodReadSchemaError(
                    f"{endpoint.name.lower()}_response_not_valid_json"
                ) from None
            try:
                parsed = response_model.model_validate(value)
            except ValidationError as exc:
                safe_error = _safe_schema_error(endpoint, exc)
                raise RobinhoodReadSchemaError(safe_error) from None
            return CapturedRead(
                endpoint=endpoint,
                request_path=path,
                received_at=received_at,
                response=parsed,
            )

        raise AssertionError("bounded request loop exited unexpectedly")

    def get_accounts(self) -> CapturedRead[AccountsResponse]:
        endpoint = RobinhoodV2ReadEndpoint.ACCOUNTS
        return self._request(endpoint, _build_path(endpoint), AccountsResponse)

    def get_trading_pair(self, symbol: str) -> CapturedRead[TradingPairsResponse]:
        symbol = _validate_cost_symbol(symbol)
        endpoint = RobinhoodV2ReadEndpoint.TRADING_PAIRS
        return self._request(
            endpoint,
            _build_path(endpoint, (("symbol", symbol),)),
            TradingPairsResponse,
        )

    def get_best_bid_ask(self, symbol: str) -> CapturedRead[BestBidAskResponse]:
        symbol = _validate_cost_symbol(symbol)
        endpoint = RobinhoodV2ReadEndpoint.BEST_BID_ASK
        return self._request(
            endpoint,
            _build_path(endpoint, (("symbol", symbol),)),
            BestBidAskResponse,
        )

    def get_estimated_price(
        self,
        symbol: str,
        quantities: Sequence[Decimal],
    ) -> CapturedRead[EstimatedPriceResponse]:
        symbol = _validate_cost_symbol(symbol)
        if not 1 <= len(quantities) <= 10:
            raise RobinhoodReadError("estimated_price_requires_one_to_ten_quantities")
        if tuple(sorted(set(quantities))) != tuple(quantities):
            raise RobinhoodReadError("estimated_price_quantities_must_be_unique_and_sorted")
        quantity_text = ",".join(_decimal_text(quantity) for quantity in quantities)
        endpoint = RobinhoodV2ReadEndpoint.ESTIMATED_PRICE
        return self._request(
            endpoint,
            _build_path(
                endpoint,
                (("symbol", symbol), ("side", "both"), ("quantity", quantity_text)),
            ),
            EstimatedPriceResponse,
        )
