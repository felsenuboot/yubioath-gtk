"""Where the derived OATH key goes when the user ticks "Remember on this computer".

libsecret (the freedesktop Secret Service) is the default on Linux. Without
it — macOS, a minimal desktop, a container — the `keyring` package covers the
macOS Keychain, Windows Credential Manager and Secret Service alike. With
neither, remembering is simply unavailable and the UI hides the switch; the
app itself still starts, which it did not when libsecret was a hard import.
"""

from __future__ import annotations

import logging
from typing import Protocol

from . import APP_ID

log = logging.getLogger(__name__)

SERVICE = "YubiKey OATH password"


class KeyStore(Protocol):
    name: str
    available: bool

    def lookup(self, device_id: str) -> bytes | None: ...
    def store(self, device_id: str, key: bytes) -> None: ...
    def clear(self, device_id: str) -> None: ...


class NullStore:
    """No secret storage on this system."""

    name = "none"
    available = False

    def lookup(self, device_id: str) -> bytes | None:
        return None

    def store(self, device_id: str, key: bytes) -> None:
        log.warning("no key store available; password not remembered")

    def clear(self, device_id: str) -> None:
        pass


class MemoryStore:
    """Process-lifetime storage, for tests and the fake backend."""

    name = "memory"
    available = True

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def lookup(self, device_id: str) -> bytes | None:
        return self._keys.get(device_id)

    def store(self, device_id: str, key: bytes) -> None:
        self._keys[device_id] = key

    def clear(self, device_id: str) -> None:
        self._keys.pop(device_id, None)


class SecretServiceStore:
    """libsecret via GObject introspection (gnome-keyring, KeePassXC, kwallet's bridge)."""

    name = "libsecret"
    available = True

    def __init__(self) -> None:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        self._secret = Secret
        self._schema = Secret.Schema.new(
            APP_ID, Secret.SchemaFlags.NONE, {"device_id": Secret.SchemaAttributeType.STRING}
        )

    def lookup(self, device_id: str) -> bytes | None:
        try:
            hexkey = self._secret.password_lookup_sync(self._schema, {"device_id": device_id}, None)
        except Exception as e:  # noqa: BLE001
            log.warning("keyring lookup failed: %s", e)
            return None
        return bytes.fromhex(hexkey) if hexkey else None

    def store(self, device_id: str, key: bytes) -> None:
        try:
            self._secret.password_store_sync(
                self._schema,
                {"device_id": device_id},
                self._secret.COLLECTION_DEFAULT,
                f"{SERVICE} ({device_id})",
                key.hex(),
                None,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("keyring store failed: %s", e)

    def clear(self, device_id: str) -> None:
        try:
            self._secret.password_clear_sync(self._schema, {"device_id": device_id}, None)
        except Exception as e:  # noqa: BLE001
            log.warning("keyring clear failed: %s", e)


class KeyringStore:
    """The `keyring` package: macOS Keychain, Windows, or Secret Service."""

    name = "keyring"
    available = True

    def __init__(self) -> None:
        import keyring
        from keyring.errors import KeyringError

        self._keyring = keyring
        self._error = KeyringError
        # A "fail" or "null" backend means keyring found nothing usable.
        backend = keyring.get_keyring()
        if type(backend).__module__.startswith("keyring.backends.fail") or "null" in type(backend).__module__:
            raise RuntimeError(f"no usable keyring backend ({type(backend).__name__})")

    def lookup(self, device_id: str) -> bytes | None:
        try:
            hexkey = self._keyring.get_password(SERVICE, device_id)
        except self._error as e:
            log.warning("keyring lookup failed: %s", e)
            return None
        return bytes.fromhex(hexkey) if hexkey else None

    def store(self, device_id: str, key: bytes) -> None:
        try:
            self._keyring.set_password(SERVICE, device_id, key.hex())
        except self._error as e:
            log.warning("keyring store failed: %s", e)

    def clear(self, device_id: str) -> None:
        try:
            self._keyring.delete_password(SERVICE, device_id)
        except self._error as e:
            log.debug("keyring clear: %s", e)


def default_store() -> KeyStore:
    """libsecret if its typelib is installed, else the keyring package, else nothing."""
    for factory in (SecretServiceStore, KeyringStore):
        try:
            store = factory()
        except Exception as e:  # noqa: BLE001
            log.debug("%s unavailable: %s", factory.__name__, e)
            continue
        log.debug("key store: %s", store.name)
        return store
    log.warning(
        "no key store available (libsecret typelib or the 'keyring' package); passwords cannot be remembered"
    )
    return NullStore()
