"""Heartbeat freshness tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from market.domain.models import utcnow


@dataclass
class HeartbeatStatus:
    ok: bool
    age_seconds: float | None
    reason: str = ""


class Heartbeat:
    def __init__(self, path: str | Path, max_age_seconds: float = 120.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_age_seconds = max_age_seconds

    def beat(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        payload = {"ts": now.astimezone(timezone.utc).isoformat()}
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def status(self, now: datetime | None = None) -> HeartbeatStatus:
        now = now or utcnow()
        if not self.path.exists():
            return HeartbeatStatus(ok=False, age_seconds=None, reason="missing")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(data["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception as exc:  # noqa: BLE001
            return HeartbeatStatus(ok=False, age_seconds=None, reason=f"corrupt:{exc}")
        age = (now - ts).total_seconds()
        if age > self.max_age_seconds:
            return HeartbeatStatus(ok=False, age_seconds=age, reason="stale")
        return HeartbeatStatus(ok=True, age_seconds=age, reason="ok")
