"""Append-only JSONL ledger."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class JsonlLedger:
    """Thread-safe append-only journal. Never truncates existing history."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.touch()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read_all(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return []
            rows: list[dict[str, Any]] = []
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rows.append(json.loads(line))
            return rows

    def extend(self, records: Iterable[dict[str, Any]]) -> None:
        for r in records:
            self.append(r)
