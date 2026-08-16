"""Public candle fetch + CSV cache (no API key).

Default source: Coinbase Exchange public candles for BTC-USD.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from market.domain.models import Candle, D, Quote

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
COINBASE_TICKER = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
COINBASE_STATS = "https://api.exchange.coinbase.com/products/BTC-USD/stats"


def fetch_coinbase_ticker(client: httpx.Client | None = None) -> Quote:
    """Live BTC-USD top-of-book from public ticker."""
    from market.domain.models import utcnow

    own = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        r = client.get(COINBASE_TICKER, headers={"User-Agent": "market-bot/0.1"})
        r.raise_for_status()
        data = r.json()
        bid = D(str(data.get("bid") or data["price"]))
        ask = D(str(data.get("ask") or data["price"]))
        if bid <= 0 or ask <= 0:
            px = D(str(data["price"]))
            bid = px
            ask = px
        if ask < bid:
            bid, ask = ask, bid
        return Quote(symbol="BTC", bid=bid, ask=ask, ts=utcnow())
    finally:
        if own:
            client.close()


def fetch_live_mark() -> tuple[Quote, dict]:
    """Return quote + raw ticker dict for logging."""
    from market.domain.models import utcnow

    own_client = httpx.Client(timeout=15.0)
    try:
        r = own_client.get(COINBASE_TICKER, headers={"User-Agent": "market-bot/0.1"})
        r.raise_for_status()
        raw = r.json()
        bid = D(str(raw.get("bid") or raw["price"]))
        ask = D(str(raw.get("ask") or raw["price"]))
        if ask < bid:
            bid, ask = ask, bid
        q = Quote(symbol="BTC", bid=bid, ask=ask, ts=utcnow())
        return q, raw
    finally:
        own_client.close()


def _parse_coinbase_row(row: list[Any]) -> Candle:
    # [ time, low, high, open, close, volume ]
    ts = datetime.fromtimestamp(int(row[0]), tz=UTC)
    return Candle(
        ts=ts,
        low=D(str(row[1])),
        high=D(str(row[2])),
        open=D(str(row[3])),
        close=D(str(row[4])),
        volume=D(str(row[5])),
    )


def fetch_coinbase_candles(
    granularity: int = 3600,
    limit_batches: int = 3,
    end: datetime | None = None,
    client: httpx.Client | None = None,
) -> list[Candle]:
    """Fetch recent BTC-USD candles. granularity seconds: 60,300,900,3600,21600,86400."""
    own = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        end = end or datetime.now(UTC)
        out: list[Candle] = []
        cursor_end = end
        # Coinbase returns max 300 candles per request
        span = timedelta(seconds=granularity * 300)
        for _ in range(limit_batches):
            start = cursor_end - span
            params: dict[str, str | int] = {
                "granularity": granularity,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": cursor_end.isoformat().replace("+00:00", "Z"),
            }
            r = client.get(
                COINBASE_CANDLES, params=params, headers={"User-Agent": "market-bot/0.1"}
            )
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            batch = [_parse_coinbase_row(row) for row in rows]
            out.extend(batch)
            oldest = min(c.ts for c in batch)
            cursor_end = oldest - timedelta(seconds=1)
        # unique + sort ascending
        by_ts = {c.ts: c for c in out}
        return [by_ts[k] for k in sorted(by_ts)]
    finally:
        if own:
            client.close()


def save_candles_csv(path: str | Path, candles: list[Candle]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow(
                [
                    c.ts.isoformat(),
                    str(c.open),
                    str(c.high),
                    str(c.low),
                    str(c.close),
                    str(c.volume),
                ]
            )


def load_candles_csv(path: str | Path) -> list[Candle]:
    path = Path(path)
    out: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = datetime.fromisoformat(row["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            out.append(
                Candle(
                    ts=ts,
                    open=D(row["open"]),
                    high=D(row["high"]),
                    low=D(row["low"]),
                    close=D(row["close"]),
                    volume=D(row.get("volume") or "0"),
                )
            )
    out.sort(key=lambda c: c.ts)
    return out


def save_candles_jsonl(path: str | Path, candles: list[Candle]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in candles:
            f.write(json.dumps(c.model_dump(mode="json"), default=str) + "\n")
