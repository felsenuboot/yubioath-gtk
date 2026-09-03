"""YubiKey OATH access. All device I/O runs on one worker thread; results are
delivered to the GTK main loop through GLib.idle_add."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

from ykman.device import list_all_devices  # noqa: E402
from yubikit.core import ApplicationNotAvailableError  # noqa: E402
from yubikit.core.smartcard import ApduError, SW, SmartCardConnection  # noqa: E402
from yubikit.oath import Code, Credential, CredentialData, OathSession  # noqa: E402

from . import APP_ID  # noqa: E402

log = logging.getLogger(__name__)
logging.getLogger("ykman").setLevel(logging.CRITICAL)

SECRET_SCHEMA = Secret.Schema.new(
    APP_ID,
    Secret.SchemaFlags.NONE,
    {"device_id": Secret.SchemaAttributeType.STRING},
)


@dataclass
class DeviceState:
    """What the UI needs to know about the current key."""

    name: str
    serial: int | None
    device_id: str | None = None
    locked: bool = False
    bad_password: bool = False


class BackendError(Exception):
    pass


class TouchTimeout(BackendError):
    pass


class Backend:
    """Serialises all YubiKey access through a single worker thread.

    Callbacks (set by the window) are always invoked on the GTK main loop:
      on_device(state | None)
      on_accounts(list[Credential], dict[bytes, Code])
      on_code(Credential, Code)
      on_error(str)
      on_service(bool)   -- False when pcscd is not reachable
    """

    POLL_INTERVAL = 1.0

    def __init__(self) -> None:
        self.on_device: Callable[[DeviceState | None], None] = lambda s: None
        self.on_accounts: Callable[[list, dict], None] = lambda c, k: None
        self.on_code: Callable[[Credential, Code], None] = lambda c, k: None
        self.on_error: Callable[[str], None] = lambda m: None
        self.on_service: Callable[[bool], None] = lambda ok: None

        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._device = None
        self._fingerprint: str | None = None
        self._key: bytes | None = None
        self._state: DeviceState | None = None
        self._service_ok: bool | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="yubikey", daemon=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- public API (callable from the main thread) ------------------------

    def refresh(self) -> None:
        self._queue.put(self._refresh)

    def unlock(self, password: str, remember: bool) -> None:
        self._queue.put(lambda: self._unlock(password, remember))

    def forget_password(self) -> None:
        self._queue.put(self._forget_password)

    def calculate(self, cred: Credential) -> None:
        self._queue.put(lambda: self._calculate(cred))

    def add(self, data: CredentialData, touch: bool, done: Callable[[str | None], None]) -> None:
        self._queue.put(lambda: self._add(data, touch, done))

    def rename(self, cred: Credential, name: str, issuer: str | None) -> None:
        self._queue.put(lambda: self._rename(cred, name, issuer))

    def delete(self, cred: Credential) -> None:
        self._queue.put(lambda: self._delete(cred))

    @property
    def state(self) -> DeviceState | None:
        return self._state

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        self._poll()
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=self.POLL_INTERVAL)
            except queue.Empty:
                self._poll()
                continue
            try:
                job()
            except Exception as e:  # noqa: BLE001
                log.exception("job failed")
                self._emit(self.on_error, self._describe(e))
                # A failing job usually means the key went away.
                self._poll(force=True)

    def _emit(self, cb, *args) -> None:
        GLib.idle_add(lambda: (cb(*args), False)[1])

    def _poll(self, force: bool = False) -> None:
        ok = _pcsc_available()
        if ok != self._service_ok:
            self._service_ok = ok
            self._emit(self.on_service, ok)
        if not ok:
            devices = []
        else:
            try:
                devices = list_all_devices([SmartCardConnection])
            except Exception as e:  # noqa: BLE001
                log.debug("list_all_devices failed: %s", e)
                devices = []
        if devices:
            dev, info = devices[0]
            fp = dev.fingerprint
        else:
            dev, info, fp = None, None, None
        if fp == self._fingerprint and not force:
            return
        self._fingerprint = fp
        self._device = dev
        self._key = None
        if dev is None:
            self._state = None
            self._emit(self.on_device, None)
            return
        name = "YubiKey"
        try:
            from ykman.scripting import get_name  # noqa: PLC0415

            name = get_name(info, dev.pid.yubikey_type if dev.pid else None)
        except Exception:  # noqa: BLE001
            pass
        self._state = DeviceState(name=name, serial=info.serial)
        self._emit(self.on_device, self._state)
        self._refresh()

    def _session(self):
        """Context manager yielding an authenticated OathSession, or raising."""
        if self._device is None:
            raise BackendError("No YubiKey connected")
        return _SessionCtx(self)

    def _refresh(self) -> None:
        if self._device is None:
            return
        try:
            with self._session() as s:
                now = int(time.time())
                result = s.calculate_all(now)
        except _Locked:
            return
        creds = sorted(result.keys(), key=_sort_key)
        codes = {c.id: code for c, code in result.items() if code is not None}
        self._emit(self.on_accounts, creds, codes)

    def _unlock(self, password: str, remember: bool) -> None:
        if self._device is None:
            return
        with self._device.open_connection(SmartCardConnection) as conn:
            s = OathSession(conn)
            key = s.derive_key(password)
            try:
                s.validate(key)
            except ApduError as e:
                if e.sw in (SW.INCORRECT_PARAMETERS, SW.SECURITY_CONDITION_NOT_SATISFIED, SW.WRONG_LENGTH):
                    self._set_locked(s.device_id, bad=True)
                    return
                raise
            self._key = key
            if remember:
                _store_key(s.device_id, key)
        self._state.locked = False
        self._state.bad_password = False
        self._emit(self.on_device, self._state)
        self._refresh()

    def _forget_password(self) -> None:
        if self._state and self._state.device_id:
            _clear_key(self._state.device_id)
        self._key = None
        self._emit(self.on_error, "Saved password removed")

    def _calculate(self, cred: Credential) -> None:
        try:
            with self._session() as s:
                try:
                    code = s.calculate_code(cred, int(time.time()))
                except ApduError as e:
                    if e.sw == SW.SECURITY_CONDITION_NOT_SATISFIED:
                        raise TouchTimeout("Touch timed out") from e
                    raise
        except _Locked:
            return
        self._emit(self.on_code, cred, code)

    def _add(self, data: CredentialData, touch: bool, done) -> None:
        try:
            with self._session() as s:
                s.put_credential(data, touch)
        except _Locked:
            self._emit(done, "YubiKey is locked")
            return
        except Exception as e:  # noqa: BLE001
            self._emit(done, self._describe(e))
            return
        self._emit(done, None)
        self._refresh()

    def _rename(self, cred: Credential, name: str, issuer: str | None) -> None:
        try:
            with self._session() as s:
                s.rename_credential(cred.id, name, issuer)
        except _Locked:
            return
        self._refresh()

    def _delete(self, cred: Credential) -> None:
        try:
            with self._session() as s:
                s.delete_credential(cred.id)
        except _Locked:
            return
        self._refresh()

    def _set_locked(self, device_id: str, bad: bool = False) -> None:
        if self._state is None:
            return
        self._state.device_id = device_id
        self._state.locked = True
        self._state.bad_password = bad
        self._emit(self.on_device, self._state)

    @staticmethod
    def _describe(e: Exception) -> str:
        if isinstance(e, TouchTimeout):
            return "Timed out waiting for touch"
        if isinstance(e, ApplicationNotAvailableError):
            return "OATH is not enabled on this YubiKey"
        if isinstance(e, ApduError):
            if e.sw == SW.NO_SPACE:
                return "No space left on the YubiKey"
            if e.sw == SW.SECURITY_CONDITION_NOT_SATISFIED:
                return "Authentication required"
            return f"YubiKey error (SW={e.sw:04x})"
        if isinstance(e, BackendError):
            return str(e)
        msg = str(e) or e.__class__.__name__
        if "not present" in msg.lower() or "removed" in msg.lower():
            return "YubiKey was removed"
        return msg


class _Locked(Exception):
    """Raised internally when the applet needs a password we don't have."""


class _SessionCtx:
    def __init__(self, backend: Backend) -> None:
        self.b = backend
        self.conn = None

    def __enter__(self) -> OathSession:
        self.conn = self.b._device.open_connection(SmartCardConnection)
        conn = self.conn.__enter__()
        try:
            s = OathSession(conn)
            if s.locked:
                key = self.b._key or _lookup_key(s.device_id)
                if key is None:
                    self.b._set_locked(s.device_id)
                    raise _Locked()
                try:
                    s.validate(key)
                except ApduError:
                    self.b._key = None
                    _clear_key(s.device_id)
                    self.b._set_locked(s.device_id, bad=True)
                    raise _Locked()
                self.b._key = key
            if self.b._state is not None:
                self.b._state.device_id = s.device_id
            return s
        except BaseException:
            self.conn.__exit__(None, None, None)
            raise

    def __exit__(self, *exc) -> None:
        self.conn.__exit__(*exc)


def _pcsc_available() -> bool:
    """True when pcscd answers. ykman swallows this error, so check directly."""
    try:
        from smartcard.System import readers  # noqa: PLC0415

        readers()
        return True
    except Exception as e:  # noqa: BLE001
        return "Service not available" not in str(e) and "0x8010001D" not in str(e)


def _sort_key(c: Credential):
    return ((c.issuer or "").lower(), c.name.lower())


# -- libsecret ---------------------------------------------------------------

def _lookup_key(device_id: str) -> bytes | None:
    try:
        hexkey = Secret.password_lookup_sync(SECRET_SCHEMA, {"device_id": device_id}, None)
    except Exception as e:  # noqa: BLE001
        log.warning("keyring lookup failed: %s", e)
        return None
    return bytes.fromhex(hexkey) if hexkey else None


def _store_key(device_id: str, key: bytes) -> None:
    try:
        Secret.password_store_sync(
            SECRET_SCHEMA,
            {"device_id": device_id},
            Secret.COLLECTION_DEFAULT,
            f"YubiKey OATH password ({device_id})",
            key.hex(),
            None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("keyring store failed: %s", e)


def _clear_key(device_id: str) -> None:
    try:
        Secret.password_clear_sync(SECRET_SCHEMA, {"device_id": device_id}, None)
    except Exception as e:  # noqa: BLE001
        log.warning("keyring clear failed: %s", e)
