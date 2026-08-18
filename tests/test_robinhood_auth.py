import base64

import keyring
import pytest
from keyring.errors import KeyringError

from market.execution.robinhood.auth import (
    API_KEY_SERVICE,
    PRIVATE_KEY_SERVICE,
    PUBLIC_KEY_SERVICE,
    MacOSKeychainStore,
    RobinhoodCredentialError,
    complete_readonly_credential,
    get_prepared_public_key,
    load_readonly_credentials,
    prepare_readonly_key,
    readonly_auth_headers,
    sign_robinhood_message,
    verify_signature,
)


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password


def _mock_macos_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_type = type("Keyring", (), {})
    backend_type.__module__ = "keyring.backends.macOS"
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend_type())


def test_official_robinhood_signature_vector_matches_byte_for_byte() -> None:
    signature = sign_robinhood_message(
        private_key_base64="xQnTJVeQLmw1/Mg2YimEViSpw/SdJcgNXZ5kQkAXNPU=",
        api_key="rh-api-6148effc-c0b1-486c-8940-a1d099456be6",
        timestamp=1698708981,
        path="/api/v1/crypto/trading/orders/",
        method="POST",
        # Robinhood's published Python vector signs str(body), including its insertion order,
        # spaces, and single quotes. The read-only client signs no request bodies.
        body=str(
            {
                "client_order_id": "131de903-5a9c-4260-abc1-28d562a5dcf0",
                "side": "buy",
                "symbol": "BTC-USD",
                "type": "market",
                "market_order_config": {"asset_quantity": "0.1"},
            }
        ),
    )

    assert signature == (
        "q/nEtxp/P2Or3hph3KejBqnw5o9qeuQ+hYRnB56FaHbjDsNUY9KhB1asMxohDnzdVFSD7StaTqjSd9U9HvaRAw=="
    )


def test_prepare_complete_and_load_keep_secrets_out_of_returned_public_record() -> None:
    store = MemorySecretStore()
    public = prepare_readonly_key("g3-test", store=store)

    assert len(base64.b64decode(public.public_key_base64, validate=True)) == 32
    assert public.public_key_fingerprint.startswith("sha256:")
    assert (PRIVATE_KEY_SERVICE, "g3-test") in store.values
    assert (PUBLIC_KEY_SERVICE, "g3-test") in store.values
    assert (API_KEY_SERVICE, "g3-test") not in store.values
    assert store.values[(PRIVATE_KEY_SERVICE, "g3-test")] not in repr(public)

    same_public = complete_readonly_credential("rh-api-readonly-test", "g3-test", store=store)
    credentials = load_readonly_credentials("g3-test", store=store)

    assert same_public == public
    assert credentials.public_key_base64 == public.public_key_base64
    assert credentials.api_key == "rh-api-readonly-test"
    assert credentials.private_key_base64 == store.values[(PRIVATE_KEY_SERVICE, "g3-test")]
    assert credentials.api_key not in repr(credentials)
    assert credentials.private_key_base64 not in repr(credentials)
    assert "[REDACTED]" in repr(credentials)


def test_get_prepared_public_key_detects_keychain_pair_mismatch() -> None:
    first = MemorySecretStore()
    second = MemorySecretStore()
    prepare_readonly_key("g3-test", store=first)
    prepare_readonly_key("g3-test", store=second)
    first.values[(PUBLIC_KEY_SERVICE, "g3-test")] = second.values[(PUBLIC_KEY_SERVICE, "g3-test")]

    with pytest.raises(RobinhoodCredentialError, match="stored_keypair_mismatch"):
        get_prepared_public_key("g3-test", store=first)


def test_credential_lifecycle_refuses_overwrite_and_missing_api_key() -> None:
    store = MemorySecretStore()
    prepare_readonly_key("g3-test", store=store)

    with pytest.raises(RobinhoodCredentialError, match="already_exists"):
        prepare_readonly_key("g3-test", store=store)
    with pytest.raises(RobinhoodCredentialError, match="complete_readonly_credential_not_found"):
        load_readonly_credentials("g3-test", store=store)

    complete_readonly_credential("rh-api-readonly-test", "g3-test", store=store)
    with pytest.raises(RobinhoodCredentialError, match="api_key_already_exists"):
        complete_readonly_credential("rh-api-replacement", "g3-test", store=store)


@pytest.mark.parametrize(
    "label",
    ["", "UPPERCASE", "space value", "../escape", "a" * 65],
)
def test_credential_label_is_path_and_keychain_safe(label: str) -> None:
    with pytest.raises(RobinhoodCredentialError, match="invalid_credential_label"):
        prepare_readonly_key(label, store=MemorySecretStore())


@pytest.mark.parametrize("api_key", ["", " short ", "has whitespace", "line\nbreak", "é" * 20])
def test_api_key_validation_fails_without_echoing_secret(api_key: str) -> None:
    store = MemorySecretStore()
    prepare_readonly_key("g3-test", store=store)

    with pytest.raises(RobinhoodCredentialError) as raised:
        complete_readonly_credential(api_key, "g3-test", store=store)

    if api_key:
        assert api_key not in str(raised.value)


def test_readonly_headers_sign_get_and_signature_verifies() -> None:
    store = MemorySecretStore()
    prepare_readonly_key("g3-test", store=store)
    complete_readonly_credential("rh-api-readonly-test", "g3-test", store=store)
    credentials = load_readonly_credentials("g3-test", store=store)
    path = "/api/v2/crypto/trading/accounts/"

    headers = readonly_auth_headers(credentials, timestamp=1_700_000_000, path=path)

    assert headers["x-api-key"] == credentials.api_key
    assert headers["x-timestamp"] == "1700000000"
    assert verify_signature(
        public_key_base64=credentials.public_key_base64,
        signature_base64=headers["x-signature"],
        api_key=credentials.api_key,
        timestamp=1_700_000_000,
        path=path,
        method="GET",
    )
    assert not verify_signature(
        public_key_base64=credentials.public_key_base64,
        signature_base64=headers["x-signature"],
        api_key=credentials.api_key,
        timestamp=1_700_000_001,
        path=path,
        method="GET",
    )


def test_signer_rejects_malformed_key_path_method_and_timestamp() -> None:
    kwargs = {
        "private_key_base64": base64.b64encode(bytes(32)).decode("ascii"),
        "api_key": "rh-api-readonly-test",
        "timestamp": 1_700_000_000,
        "path": "/api/v2/crypto/trading/accounts/",
        "method": "GET",
    }
    with pytest.raises(RobinhoodCredentialError, match="private_key_length"):
        sign_robinhood_message(**{**kwargs, "private_key_base64": "AA=="})
    with pytest.raises(RobinhoodCredentialError, match="signature_path"):
        sign_robinhood_message(**{**kwargs, "path": "https://evil.example/api/"})
    with pytest.raises(RobinhoodCredentialError, match="signature_method"):
        sign_robinhood_message(**{**kwargs, "method": "get"})
    with pytest.raises(RobinhoodCredentialError, match="signature_timestamp"):
        sign_robinhood_message(**{**kwargs, "timestamp": 0})


def test_keychain_adapter_rejects_non_macos_and_unexpected_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    with pytest.raises(RobinhoodCredentialError, match="macos_keychain_required"):
        MacOSKeychainStore()

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(keyring, "get_keyring", object)
    with pytest.raises(RobinhoodCredentialError, match="unexpected_system_keyring_backend"):
        MacOSKeychainStore()


def test_keychain_backend_errors_are_sanitized_and_not_chained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_macos_backend(monkeypatch)
    store = MacOSKeychainStore()
    secret = "must-not-escape-keychain-error"

    def fail_get(service: str, username: str) -> str | None:
        raise KeyringError(secret)

    monkeypatch.setattr(keyring, "get_password", fail_get)
    with pytest.raises(RobinhoodCredentialError, match="keychain_read_failed") as read_error:
        store.get_password("service", "user")
    assert secret not in str(read_error.value)
    assert read_error.value.__cause__ is None

    def fail_set(service: str, username: str, password: str) -> None:
        raise KeyringError(secret)

    monkeypatch.setattr(keyring, "set_password", fail_set)
    with pytest.raises(RobinhoodCredentialError, match="keychain_write_failed") as write_error:
        store.set_password("service", "user", secret)
    assert secret not in str(write_error.value)
    assert write_error.value.__cause__ is None
