"""Closed Coinbase candle ingestion and deterministic normalized serialization."""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from market.data.quality import (
    DatasetQualityError,
    contains_only_declared_gaps,
    validate_candles,
)
from market.domain.models import Candle, D, DataQualityFlag, Quote, Timeframe, utcnow

COINBASE_PRODUCT = "BTC-USD"
COINBASE_SOURCE = "coinbase-exchange:BTC-USD"
COINBASE_CANDLES = f"https://api.exchange.coinbase.com/products/{COINBASE_PRODUCT}/candles"
COINBASE_TICKER = f"https://api.exchange.coinbase.com/products/{COINBASE_PRODUCT}/ticker"
COINBASE_STATS = f"https://api.exchange.coinbase.com/products/{COINBASE_PRODUCT}/stats"
MAX_CANDLES_PER_REQUEST = 300
# Coinbase treats range boundaries inclusively in some responses. Request one fewer interval so
# boundary extras cannot crowd an in-range candle out of the documented 300-point response cap.
CANDLES_PER_RANGE_REQUEST = MAX_CANDLES_PER_REQUEST - 1
CLOSE_CONFIRMATION_GRACE_SECONDS = 30
CANDLE_CSV_FIELDS = (
    "schema_version",
    "ts",
    "timeframe",
    "source",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "received_at",
    "close_confirmed_at",
    "is_closed",
    "quality_flags",
)


def _get_with_retry(
    client: httpx.Client,
    *,
    params: dict[str, str | int],
    max_attempts: int = 4,
) -> httpx.Response:
    last_error: httpx.TransportError | None = None
    for attempt in range(max_attempts):
        try:
            response = client.get(
                COINBASE_CANDLES,
                params=params,
                headers={"User-Agent": "market-bot/0.1"},
            )
        except httpx.TransportError as exc:
            last_error = exc
        else:
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response
            if attempt == max_attempts - 1:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    time.sleep(min(float(retry_after), 10.0))
                    continue
                except ValueError:
                    pass
        if attempt < max_attempts - 1:
            time.sleep(2**attempt)
    if last_error is not None:
        raise last_error
    raise RuntimeError("candle request retry loop exited unexpectedly")


@dataclass(frozen=True)
class RawCandleBatch:
    request_start: datetime
    request_end: datetime
    rows: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class CoinbaseCandleFetch:
    product: str
    source: str
    timeframe: Timeframe
    requested_start: datetime
    requested_end: datetime
    retrieved_at: datetime
    candles: tuple[Candle, ...]
    raw_batches: tuple[RawCandleBatch, ...]


def _require_utc_boundary(value: datetime, timeframe: Timeframe, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must use UTC")
    value = value.astimezone(UTC)
    if int(value.timestamp()) % timeframe.seconds != 0 or value.microsecond != 0:
        raise ValueError(f"{name} must align to a {timeframe.value} boundary")
    return value


def latest_closed_boundary(
    as_of: datetime,
    timeframe: Timeframe = Timeframe.HOUR_1,
    *,
    grace_seconds: int = CLOSE_CONFIRMATION_GRACE_SECONDS,
) -> datetime:
    """Exclusive range end that cannot include the currently forming bar."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    safe_time = as_of.astimezone(UTC) - timedelta(seconds=grace_seconds)
    epoch = int(safe_time.timestamp())
    boundary = epoch - epoch % timeframe.seconds
    return datetime.fromtimestamp(boundary, tz=UTC)


def fetch_coinbase_ticker(client: httpx.Client | None = None) -> Quote:
    """Live BTC-USD top-of-book from the public Exchange ticker."""
    own = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        response = client.get(COINBASE_TICKER, headers={"User-Agent": "market-bot/0.1"})
        response.raise_for_status()
        data = response.json()
        bid = D(str(data.get("bid") or data["price"]))
        ask = D(str(data.get("ask") or data["price"]))
        if bid <= 0 or ask <= 0:
            price = D(str(data["price"]))
            bid = price
            ask = price
        if ask < bid:
            bid, ask = ask, bid
        return Quote(symbol="BTC", bid=bid, ask=ask, ts=utcnow())
    finally:
        if own:
            client.close()


def fetch_live_mark() -> tuple[Quote, dict[str, Any]]:
    """Return quote plus the raw public ticker response for journaling."""
    client = httpx.Client(timeout=15.0)
    try:
        response = client.get(COINBASE_TICKER, headers={"User-Agent": "market-bot/0.1"})
        response.raise_for_status()
        raw: dict[str, Any] = response.json()
        bid = D(str(raw.get("bid") or raw["price"]))
        ask = D(str(raw.get("ask") or raw["price"]))
        if ask < bid:
            bid, ask = ask, bid
        return Quote(symbol="BTC", bid=bid, ask=ask, ts=utcnow()), raw
    finally:
        client.close()


def _parse_coinbase_row(
    row: tuple[Any, ...],
    *,
    retrieved_at: datetime,
    timeframe: Timeframe,
) -> Candle:
    if len(row) != 6:
        raise ValueError(f"invalid Coinbase candle row length: {len(row)}")
    # Coinbase Exchange schema: [time, low, high, open, close, volume].
    ts = datetime.fromtimestamp(int(row[0]), tz=UTC)
    close_time = ts + timedelta(seconds=timeframe.seconds)
    # For historical closed candles, close_confirmed_at should be the candle's close time
    # (or later). Using retrieved_at here can violate close_confirmed_at >= close_time
    # if retrieved_at is before the candle's actual close (e.g., near boundary).
    return Candle(
        ts=ts,
        timeframe=timeframe,
        source=COINBASE_SOURCE,
        low=D(str(row[1])),
        high=D(str(row[2])),
        open=D(str(row[3])),
        close=D(str(row[4])),
        volume=D(str(row[5])),
        received_at=retrieved_at,
        close_confirmed_at=close_time,
        is_closed=True,
    )


def fetch_coinbase_candle_range(
    start: datetime,
    end: datetime,
    *,
    granularity: int = 3600,
    as_of: datetime | None = None,
    client: httpx.Client | None = None,
    allow_declared_gaps: bool = False,
) -> CoinbaseCandleFetch:
    """Fetch a complete, closed, half-open ``[start, end)`` BTC-USD range.

    Coinbase documents a 300-candle request maximum and may omit no-tick buckets. Each response
    is preserved in ``raw_batches``; the normalized result is sorted but never deduplicated or
    forward-filled, so duplicates and gaps fail the quality gate.
    """
    timeframe = Timeframe.from_seconds(granularity)
    start = _require_utc_boundary(start, timeframe, "start")
    end = _require_utc_boundary(end, timeframe, "end")
    if end <= start:
        raise ValueError("end must be after start")

    observed_at = as_of or utcnow()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    retrieved_at = observed_at.astimezone(UTC)
    if end > latest_closed_boundary(retrieved_at, timeframe):
        raise ValueError("requested range includes an unclosed or not-yet-confirmed candle")

    own = client is None
    client = client or httpx.Client(timeout=30.0)
    raw_batches: list[RawCandleBatch] = []
    parsed: list[Candle] = []
    batch_span = timedelta(seconds=timeframe.seconds * CANDLES_PER_RANGE_REQUEST)
    cursor = start
    try:
        while cursor < end:
            request_end = min(cursor + batch_span, end)
            params: dict[str, str | int] = {
                "granularity": timeframe.seconds,
                "start": cursor.isoformat().replace("+00:00", "Z"),
                "end": request_end.isoformat().replace("+00:00", "Z"),
            }
            response = _get_with_retry(client, params=params)
            payload = response.json()
            if not isinstance(payload, list):
                raise TypeError("Coinbase candle response must be a list")
            rows = tuple(tuple(row) for row in payload)
            raw_batches.append(
                RawCandleBatch(request_start=cursor, request_end=request_end, rows=rows)
            )
            for row in rows:
                candle = _parse_coinbase_row(
                    row,
                    retrieved_at=retrieved_at,
                    timeframe=timeframe,
                )
                # Coinbase may return bars before the declared start. The raw response retains
                # them, while normalized data obeys the exact half-open request range.
                if cursor <= candle.ts < request_end and start <= candle.ts < end:
                    parsed.append(candle)
            cursor = request_end
    finally:
        if own:
            client.close()

    parsed.sort(key=lambda candle: candle.ts)
    report = validate_candles(
        parsed,
        as_of=retrieved_at,
        expected_start=start,
        expected_end=end,
    )
    if not report.ok and not (allow_declared_gaps and contains_only_declared_gaps(report)):
        raise DatasetQualityError(report)
    return CoinbaseCandleFetch(
        product=COINBASE_PRODUCT,
        source=COINBASE_SOURCE,
        timeframe=timeframe,
        requested_start=start,
        requested_end=end,
        retrieved_at=retrieved_at,
        candles=tuple(parsed),
        raw_batches=tuple(raw_batches),
    )


def fetch_coinbase_candles(
    granularity: int = 3600,
    limit_batches: int = 3,
    end: datetime | None = None,
    client: httpx.Client | None = None,
    *,
    as_of: datetime | None = None,
) -> list[Candle]:
    """Compatibility wrapper for recent, fully closed hourly candles."""
    if limit_batches <= 0:
        raise ValueError("limit_batches must be > 0")
    timeframe = Timeframe.from_seconds(granularity)
    observed = as_of or utcnow()
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    observed_at = observed.astimezone(UTC)
    range_end = (
        _require_utc_boundary(end, timeframe, "end")
        if end is not None
        else latest_closed_boundary(observed_at, timeframe)
    )
    start = range_end - timedelta(
        seconds=timeframe.seconds * MAX_CANDLES_PER_REQUEST * limit_batches
    )
    result = fetch_coinbase_candle_range(
        start,
        range_end,
        granularity=granularity,
        as_of=observed_at,
        client=client,
    )
    return list(result.candles)


def _timestamp_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def candles_csv_bytes(candles: list[Candle] | tuple[Candle, ...]) -> bytes:
    """Canonical normalized CSV bytes used for content addressing."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CANDLE_CSV_FIELDS)
    for candle in candles:
        writer.writerow(
            [
                candle.schema_version,
                _timestamp_text(candle.ts),
                candle.timeframe.value,
                candle.source,
                _decimal_text(candle.open),
                _decimal_text(candle.high),
                _decimal_text(candle.low),
                _decimal_text(candle.close),
                _decimal_text(candle.volume),
                _timestamp_text(candle.received_at),
                _timestamp_text(candle.close_confirmed_at),
                "true" if candle.is_closed else "false",
                "|".join(flag.value for flag in candle.quality_flags),
            ]
        )
    return buffer.getvalue().encode("utf-8")


def save_candles_csv(path: str | Path, candles: list[Candle] | tuple[Candle, ...]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(candles_csv_bytes(candles))


def _parse_csv_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("CSV candle timestamps must be timezone-aware UTC")
    return parsed


def load_candles_csv(path: str | Path) -> list[Candle]:
    """Load without sorting so sequence defects remain visible to the quality checker."""
    out: list[Candle] = []
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            flags = tuple(
                DataQualityFlag(item)
                for item in (row.get("quality_flags") or "").split("|")
                if item
            )
            is_closed_text = row.get("is_closed")
            if is_closed_text is None:
                is_closed = True
            elif is_closed_text in {"true", "false"}:
                is_closed = is_closed_text == "true"
            else:
                raise ValueError(f"invalid is_closed value: {is_closed_text!r}")
            received_text = row.get("received_at") or ""
            confirmed_text = row.get("close_confirmed_at") or ""
            out.append(
                Candle(
                    schema_version=int(row.get("schema_version") or 1),
                    ts=_parse_csv_timestamp(row["ts"]),
                    timeframe=Timeframe(row.get("timeframe") or Timeframe.HOUR_1.value),
                    source=row.get("source") or "legacy-csv",
                    open=D(row["open"]),
                    high=D(row["high"]),
                    low=D(row["low"]),
                    close=D(row["close"]),
                    volume=D(row.get("volume") or "0"),
                    received_at=_parse_csv_timestamp(received_text) if received_text else None,
                    close_confirmed_at=(
                        _parse_csv_timestamp(confirmed_text) if confirmed_text else None
                    ),
                    is_closed=is_closed,
                    quality_flags=flags,
                )
            )
    return out


def save_candles_jsonl(path: str | Path, candles: list[Candle]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for candle in candles:
            file.write(json.dumps(candle.model_dump(mode="json"), default=str) + "\n")
