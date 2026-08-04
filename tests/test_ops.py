from datetime import datetime, timedelta, timezone
from pathlib import Path

from market.ops.freeze import FreezeControl
from market.ops.heartbeat import Heartbeat


def test_freeze_file(tmp_path: Path):
    f = FreezeControl(tmp_path / "FREEZE")
    assert not f.frozen
    f.freeze("manual")
    assert f.frozen
    assert "manual" in f.path.read_text()
    f.unfreeze()
    assert not f.frozen


def test_heartbeat_fresh_and_stale(tmp_path: Path):
    hb = Heartbeat(tmp_path / "hb.json", max_age_seconds=60)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not hb.status(now).ok
    hb.beat(now)
    assert hb.status(now + timedelta(seconds=10)).ok
    st = hb.status(now + timedelta(seconds=120))
    assert not st.ok
    assert st.reason == "stale"
