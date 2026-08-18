"""Robinhood Crypto API signing and macOS Keychain credential references.

The module never accepts credentials on command-line arguments or from environment variables.
Private key material and API keys are stored through the system keyring backend and are exposed
only to the in-memory signer. Network access and endpoint authorization live in ``read_client``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import platform
import re
from dataclasses import dataclass
from typing import Protocol

import keyring
from keyring.errors import KeyringError
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

PRIVATE_KEY_SERVICE = "market.robinhood.readonly.private-key"
PUBLIC_KEY_SERVICE = "market.robinhood.readonly.public-key"
API_KEY_SERVICE = "market.robinhood.readonly.api-key"
DEFAULT_READONLY_CREDENTIAL_LABEL = "g3-cost-study-v1"
_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class RobinhoodCredentialError(RuntimeError):
    """A local credential is absent, malformed, inconsistent, or stored unsafely."""


class SecretStore(Protocol):
    """Minimal password-store interface used by the credential lifecycle."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


class MacOSKeychainStore:
    """System-keyring adapter that refuses non-macOS or non-Keychain backends."""

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            raise RobinhoodCredentialError("macos_keychain_required")
        try:
            backend = keyring.get_keyring()
        except KeyringError:
            raise RobinhoodCredentialError("system_keyring_unavailable") from None
        backend_identity = f"{type(backend).__module__}.{type(backend).__qualname__}".lower()
        if "macos" not in backend_identity:
            raise RobinhoodCredentialError("unexpected_system_keyring_backend")

    def get_password(self, service: str, username: str) -> str | None:
        try:
            return keyring.get_password(service, username)
        except KeyringError:
            raise RobinhoodCredentialError("keychain_read_failed") from None

    def set_password(self, service: str, username: str, password: str) -> None:
        try:
            keyring.set_password(service, username, password)
        except KeyringError:
            raise RobinhoodCredentialError("keychain_write_failed") from None


@dataclass(frozen=True)
class PreparedPublicKey:
    credential_label: str
    public_key_base64: str
    public_key_fingerprint: str


@dataclass(frozen=True)
class RobinhoodReadCredentials:
    credential_label: str
    api_key: str
    private_key_base64: str
    public_key_base64: str
    public_key_fingerprint: str

    def __repr__(self) -> str:
        return (
            "RobinhoodReadCredentials(credential_label="
            f"{self.credential_label!r}, api_key='[REDACTED]', "
            "private_key_base64='[REDACTED]', "
            f"public_key_fingerprint={self.public_key_fingerprint!r})"
        )


def validate_credential_label(value: str) -> str:
    if not _LABEL_PATTERN.fullmatch(value):
        raise RobinhoodCredentialError("invalid_credential_label")
    return value


def validate_api_key(value: str) -> str:
    if not 8 <= len(value) <= 256:
        raise RobinhoodCredentialError("invalid_api_key_length")
    if value.strip() != value or any(character.isspace() for character in value):
        raise RobinhoodCredentialError("invalid_api_key_whitespace")
    if not value.isascii() or any(
        ord(character) < 33 or ord(character) > 126 for character in value
    ):
        raise RobinhoodCredentialError("invalid_api_key_characters")
    return value


def _decode_base64_key(value: str, *, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RobinhoodCredentialError(f"malformed_{label}") from exc
    if len(decoded) != 32:
        raise RobinhoodCredentialError(f"invalid_{label}_length")
    return decoded


def _public_key_fingerprint(public_key: bytes) -> str:
    return f"sha256:{hashlib.sha256(public_key).hexdigest()}"


def _prepared_public_key(credential_label: str, public_key: bytes) -> PreparedPublicKey:
    return PreparedPublicKey(
        credential_label=credential_label,
        public_key_base64=base64.b64encode(public_key).decode("ascii"),
        public_key_fingerprint=_public_key_fingerprint(public_key),
    )


def prepare_readonly_key(
    credential_label: str = DEFAULT_READONLY_CREDENTIAL_LABEL,
    *,
    store: SecretStore | None = None,
) -> PreparedPublicKey:
    """Generate a new Ed25519 seed and store it without printing or returning the seed."""
    label = validate_credential_label(credential_label)
    secret_store = store or MacOSKeychainStore()
    if any(
        secret_store.get_password(service, label) is not None
        for service in (PRIVATE_KEY_SERVICE, PUBLIC_KEY_SERVICE, API_KEY_SERVICE)
    ):
        raise RobinhoodCredentialError("credential_label_already_exists")

    signing_key = SigningKey.generate()
    private_key_base64 = base64.b64encode(signing_key.encode()).decode("ascii")
    public = _prepared_public_key(label, signing_key.verify_key.encode())
    secret_store.set_password(PRIVATE_KEY_SERVICE, label, private_key_base64)
    secret_store.set_password(PUBLIC_KEY_SERVICE, label, public.public_key_base64)
    return public


def get_prepared_public_key(
    credential_label: str = DEFAULT_READONLY_CREDENTIAL_LABEL,
    *,
    store: SecretStore | None = None,
) -> PreparedPublicKey:
    label = validate_credential_label(credential_label)
    secret_store = store or MacOSKeychainStore()
    private_value = secret_store.get_password(PRIVATE_KEY_SERVICE, label)
    public_value = secret_store.get_password(PUBLIC_KEY_SERVICE, label)
    if private_value is None or public_value is None:
        raise RobinhoodCredentialError("prepared_key_not_found")

    signing_key = SigningKey(_decode_base64_key(private_value, label="private_key"))
    derived_public = signing_key.verify_key.encode()
    stored_public = _decode_base64_key(public_value, label="public_key")
    if derived_public != stored_public:
        raise RobinhoodCredentialError("stored_keypair_mismatch")
    return _prepared_public_key(label, derived_public)


def complete_readonly_credential(
    api_key: str,
    credential_label: str = DEFAULT_READONLY_CREDENTIAL_LABEL,
    *,
    store: SecretStore | None = None,
) -> PreparedPublicKey:
    """Bind a Robinhood-issued API key to a prepared Keychain signing key."""
    label = validate_credential_label(credential_label)
    secret_store = store or MacOSKeychainStore()
    public = get_prepared_public_key(label, store=secret_store)
    if secret_store.get_password(API_KEY_SERVICE, label) is not None:
        raise RobinhoodCredentialError("api_key_already_exists")
    secret_store.set_password(API_KEY_SERVICE, label, validate_api_key(api_key))
    return public


def load_readonly_credentials(
    credential_label: str = DEFAULT_READONLY_CREDENTIAL_LABEL,
    *,
    store: SecretStore | None = None,
) -> RobinhoodReadCredentials:
    label = validate_credential_label(credential_label)
    secret_store = store or MacOSKeychainStore()
    public = get_prepared_public_key(label, store=secret_store)
    private_value = secret_store.get_password(PRIVATE_KEY_SERVICE, label)
    api_key = secret_store.get_password(API_KEY_SERVICE, label)
    if private_value is None or api_key is None:
        raise RobinhoodCredentialError("complete_readonly_credential_not_found")
    validate_api_key(api_key)
    return RobinhoodReadCredentials(
        credential_label=label,
        api_key=api_key,
        private_key_base64=private_value,
        public_key_base64=public.public_key_base64,
        public_key_fingerprint=public.public_key_fingerprint,
    )


def sign_robinhood_message(
    *,
    private_key_base64: str,
    api_key: str,
    timestamp: int,
    path: str,
    method: str,
    body: str = "",
) -> str:
    """Produce Robinhood's documented base64 Ed25519 request signature."""
    validate_api_key(api_key)
    if timestamp <= 0:
        raise RobinhoodCredentialError("invalid_signature_timestamp")
    if not path.startswith("/api/") or "\n" in path or "\r" in path:
        raise RobinhoodCredentialError("invalid_signature_path")
    normalized_method = method.upper()
    if method != normalized_method or normalized_method not in {"GET", "POST"}:
        raise RobinhoodCredentialError("invalid_signature_method")
    private_key = _decode_base64_key(private_key_base64, label="private_key")
    message = f"{api_key}{timestamp}{path}{normalized_method}{body}".encode()
    signature = SigningKey(private_key).sign(message).signature
    return base64.b64encode(signature).decode("ascii")


def verify_signature(
    *,
    public_key_base64: str,
    signature_base64: str,
    api_key: str,
    timestamp: int,
    path: str,
    method: str,
    body: str = "",
) -> bool:
    """Verify a signature without exposing either credential value in errors."""
    public_key = _decode_base64_key(public_key_base64, label="public_key")
    try:
        signature = base64.b64decode(signature_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RobinhoodCredentialError("malformed_signature") from exc
    message = f"{api_key}{timestamp}{path}{method}{body}".encode()
    try:
        VerifyKey(public_key).verify(message, signature)
    except (BadSignatureError, ValueError):
        return False
    return True


def readonly_auth_headers(
    credentials: RobinhoodReadCredentials,
    *,
    timestamp: int,
    path: str,
) -> dict[str, str]:
    """Create authentication headers for a bodyless GET request only."""
    signature = sign_robinhood_message(
        private_key_base64=credentials.private_key_base64,
        api_key=credentials.api_key,
        timestamp=timestamp,
        path=path,
        method="GET",
    )
    return {
        "x-api-key": credentials.api_key,
        "x-signature": signature,
        "x-timestamp": str(timestamp),
        "Content-Type": "application/json; charset=utf-8",
    }
