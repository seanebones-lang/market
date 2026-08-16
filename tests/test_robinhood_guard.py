import pytest

from market.domain.models import Intent, OrderAck, OrderStatus, OrderType, Side, utcnow
from market.execution.robinhood import (
    RobinhoodAuthError,
    RobinhoodBroker,
    RobinhoodLiveDisabled,
    RobinhoodSession,
    rh_live_unlocked,
)


def test_rh_live_unlocked_default_off(monkeypatch):
    monkeypatch.delenv("MARKET_RH_LIVE", raising=False)
    assert rh_live_unlocked() is False


@pytest.mark.parametrize("live_env", ["0", "1"])
@pytest.mark.parametrize("mode_is_live", [False, True])
@pytest.mark.parametrize("allow_live", [False, True])
def test_live_transport_disabled_by_build(monkeypatch, live_env, mode_is_live, allow_live):
    monkeypatch.setenv("MARKET_RH_LIVE", live_env)
    called = 0

    def transport(intent: Intent) -> OrderAck:
        nonlocal called
        called += 1
        return OrderAck(
            client_order_id=intent.client_order_id,
            broker_order_id="must-not-run",
            status=OrderStatus.FILLED,
            side=intent.side,
            qty_btc=intent.qty_btc,
            order_type=OrderType.MARKET,
            ts=utcnow(),
        )

    b = RobinhoodBroker(
        allow_live=allow_live,
        mode_is_live=mode_is_live,
        transport_place=transport,
    )
    b.session.configure("rh-api-test", "/outside/repository/private-key")
    intent = Intent(side=Side.BUY, qty_btc="0.001", reason="t")
    with pytest.raises(RobinhoodLiveDisabled, match="disabled_by_build"):
        b.place_order(intent)
    assert called == 0


def test_auth_error_callback():
    seen: list[str] = []

    def on_err(exc: Exception) -> None:
        seen.append(str(exc))

    b = RobinhoodBroker(on_auth_error=on_err)
    with pytest.raises(RobinhoodAuthError):
        b.get_balances()
    assert seen


def test_session_rejects_missing_official_credentials():
    s = RobinhoodSession()
    with pytest.raises(RobinhoodAuthError, match="missing_credentials"):
        s.configure("", "")
    assert not s.configured


def test_session_configures_official_credential_reference():
    s = RobinhoodSession()
    s.configure("rh-api-test", "/outside/repository/private-key")
    s.ensure_auth()
    assert s.configured
    assert s.api_key == "rh-api-test"
