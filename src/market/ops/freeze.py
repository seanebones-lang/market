"""Freeze flag file helpers."""

from __future__ import annotations

from pathlib import Path


class FreezeControl:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def frozen(self) -> bool:
        return self.path.exists()

    def freeze(self, reason: str = "") -> None:
        self.path.write_text(reason or "freeze", encoding="utf-8")

    def unfreeze(self) -> None:
        if self.path.exists():
            self.path.unlink()
