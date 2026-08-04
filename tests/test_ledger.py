from pathlib import Path

from market.ledger.jsonl import JsonlLedger


def test_append_and_read(tmp_path: Path):
    led = JsonlLedger(tmp_path / "x.jsonl")
    led.append({"a": 1})
    led.append({"b": 2})
    rows = led.read_all()
    assert rows == [{"a": 1}, {"b": 2}]


def test_does_not_wipe_history(tmp_path: Path):
    path = tmp_path / "y.jsonl"
    led = JsonlLedger(path)
    led.append({"n": 1})
    led2 = JsonlLedger(path)
    led2.append({"n": 2})
    assert len(led2.read_all()) == 2
