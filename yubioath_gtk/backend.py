"""YubiKey OATH access. All device I/O runs on one worker thread; results are
delivered to the GTK main loop through GLib.idle_add."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

from ykman.device import list_all_devices  # noqa: E402
from yubikit.core import ApplicationNotAvailableError  # noqa: E402
from yubikit.core.smartcard import ApduError, SW, SmartCardConnection  # noqa: E402
from yubikit.management import CAPABILITY, FORM_FACTOR, TRANSPORT  # noqa: E402
from yubikit.oath import Code, Credential, CredentialData, OathSession  # noqa: E402

from . import APP_ID  # noqa: E402
from .config import config  # noqa: E402

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
    busy: bool = False  # another process holds the CCID interface
    has_password: bool = False
    fingerprint: str | None = None
    version: str = ""
    form_factor: str = ""
    is_fips: bool = False
    is_sky: bool = False
    # transport name -> {application name: enabled}; only transports the key supports
    applications: dict[str, dict[str, bool]] | None = None


@dataclass
class DeviceSummary:
    fingerprint: str
    name: str
    serial: int | None

    @property
    def label(self) -> str:
        return f"{self.name} · {self.serial}" if self.serial else self.name


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
      on_devices(list[DeviceSummary], active_fingerprint | None)
    """

    POLL_INTERVAL = 1.0

    def __init__(self) -> None:
        self.on_device: Callable[[DeviceState | None], None] = lambda s: None
        self.on_accounts: Callable[[list, dict], None] = lambda c, k: None
        self.on_code: Callable[[Credential, Code], None] = lambda c, k: None
        self.on_error: Callable[[str], None] = lambda m: None
        self.on_service: Callable[[bool], None] = lambda ok: None
        self.on_devices: Callable[[list, str | None], None] = lambda d, a: None

        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._device = None
        self._fingerprint: str | None = None
        self._key: bytes | None = None
        self._state: DeviceState | None = None
        self._service_ok: bool | None = None
        self._selected_fp: str | None = None
        self._all_fps: tuple = ()
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

    def select_device(self, fingerprint: str) -> None:
        def job():
            self._selected_fp = fingerprint
            self._poll(force=True)

        self._queue.put(job)

    def set_password(self, password: str, remember: bool, done: Callable[[str | None], None]) -> None:
        self._queue.put(lambda: self._set_password(password, remember, done))

    def remove_password(self, done: Callable[[str | None], None]) -> None:
        self._queue.put(lambda: self._remove_password(done))

    def reset_oath(self, done: Callable[[str | None], None]) -> None:
        self._queue.put(lambda: self._reset_oath(done))

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
            except Exception as e:
                log.exception("job failed")
                self._emit(self.on_error, self._describe(e))
                # A failing job usually means the key went away.
                self._poll(force=True)

    def _emit(self, cb, *args) -> None:
        """Dispatch to the main loop. Callbacks are resolved by attribute name at
        dispatch time so a handler assigned after the thread started still runs."""
        name = next(
            (
                n
                for n in ("on_device", "on_accounts", "on_code", "on_error", "on_service", "on_devices")
                if getattr(self, n) is cb
            ),
            None,
        )
        if name:
            GLib.idle_add(lambda: (getattr(self, name)(*args), False)[1])
        else:
            GLib.idle_add(lambda: (cb(*args), False)[1])

    def _poll(self, force: bool = False) -> None:
        ok = _pcsc_available()
        if ok != self._service_ok:
            self._service_ok = ok
            self._emit(self.on_service, ok)
        devices = []
        if ok:
            try:
                devices = list_all_devices([SmartCardConnection])
            except Exception as e:  # noqa: BLE001
                log.debug("list_all_devices failed: %s", e)
        summaries = [DeviceSummary(d.fingerprint, _device_name(d, i), i.serial) for d, i in devices]
        fps = tuple(x.fingerprint for x in summaries)

        # Pick the active key: explicit selection, else last used serial, else first.
        chosen = None
        if self._selected_fp in fps:
            chosen = self._selected_fp
        elif fps:
            last = config.get("last_serial")
            chosen = next((x.fingerprint for x in summaries if last and x.serial == last), fps[0])
        if fps != self._all_fps or chosen != self._selected_fp:
            self._all_fps = fps
            self._selected_fp = chosen
            self._emit(self.on_devices, summaries, chosen)

        fp = chosen
        dev, info = next(((d, i) for d, i in devices if d.fingerprint == fp), (None, None))
        if dev is None and ok and _yubikey_busy():
            fp = "busy"
        log.debug("poll: service=%s devices=%d fp=%r prev=%r", ok, len(devices), fp, self._fingerprint)
        if fp == self._fingerprint and not force:
            return
        if fp == "busy":
            self._fingerprint = fp
            self._device = None
            self._key = None
            self._state = DeviceState(name="YubiKey", serial=None, busy=True)
            self._emit(self.on_device, self._state)
            return
        self._fingerprint = fp
        self._device = dev
        self._key = None
        if dev is None:
            self._state = None
            self._emit(self.on_device, None)
            return
        if info.serial:
            config.set("last_serial", info.serial)
        self._state = DeviceState(
            name=_device_name(dev, info),
            serial=info.serial,
            fingerprint=dev.fingerprint,
            version=str(info.version),
            form_factor=_FORM_FACTORS.get(info.form_factor, "Unknown"),
            is_fips=bool(info.is_fips),
            is_sky=bool(info.is_sky),
            applications=_applications(info),
        )
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

    def _set_password(self, password: str, remember: bool, done) -> None:
        try:
            with self._session() as s:
                key = s.derive_key(password)
                s.set_key(key)
                self._key = key
                if remember or _lookup_key(s.device_id) is not None:
                    _store_key(s.device_id, key)
                else:
                    _clear_key(s.device_id)
        except _Locked:
            self._emit(done, "YubiKey is locked")
            return
        except Exception as e:  # noqa: BLE001
            self._emit(done, self._describe(e))
            return
        self._state.has_password = True
        self._emit(self.on_device, self._state)
        self._emit(done, None)

    def _remove_password(self, done) -> None:
        try:
            with self._session() as s:
                s.unset_key()
                _clear_key(s.device_id)
                self._key = None
        except _Locked:
            self._emit(done, "YubiKey is locked")
            return
        except Exception as e:  # noqa: BLE001
            self._emit(done, self._describe(e))
            return
        self._state.has_password = False
        self._emit(self.on_device, self._state)
        self._emit(done, None)

    def _reset_oath(self, done) -> None:
        """Wipes the OATH applet. Needs no password, so bypass the auth wrapper."""
        if self._device is None:
            self._emit(done, "No YubiKey connected")
            return
        try:
            with self._device.open_connection(SmartCardConnection) as conn:
                s = OathSession(conn)
                device_id = s.device_id
                s.reset()
            _clear_key(device_id)
            self._key = None
        except Exception as e:  # noqa: BLE001
            self._emit(done, self._describe(e))
            return
        self._state.has_password = False
        self._state.locked = False
        self._emit(self.on_device, self._state)
        self._emit(done, None)
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
                    raise _Locked() from None
                self.b._key = key
            if self.b._state is not None:
                self.b._state.device_id = s.device_id
                self.b._state.has_password = bool(s.has_key)
            return s
        except BaseException:
            self.conn.__exit__(None, None, None)
            raise

    def __exit__(self, *exc) -> None:
        self.conn.__exit__(*exc)


_FORM_FACTORS = {
    FORM_FACTOR.USB_A_KEYCHAIN: "USB-A Keychain",
    FORM_FACTOR.USB_A_NANO: "USB-A Nano",
    FORM_FACTOR.USB_C_KEYCHAIN: "USB-C Keychain",
    FORM_FACTOR.USB_C_NANO: "USB-C Nano",
    FORM_FACTOR.USB_C_LIGHTNING: "USB-C / Lightning",
    FORM_FACTOR.USB_A_BIO: "USB-A Bio",
    FORM_FACTOR.USB_C_BIO: "USB-C Bio",
}
_APP_NAMES = [
    (CAPABILITY.OTP, "Yubico OTP"),
    (CAPABILITY.U2F, "FIDO U2F"),
    (CAPABILITY.FIDO2, "FIDO2"),
    (CAPABILITY.OATH, "OATH"),
    (CAPABILITY.PIV, "PIV"),
    (CAPABILITY.OPENPGP, "OpenPGP"),
    (CAPABILITY.HSMAUTH, "YubiHSM Auth"),
]


def _device_name(dev, info) -> str:
    try:
        from ykman.scripting import get_name

        return get_name(info, dev.pid.yubikey_type if dev.pid else None)
    except Exception:  # noqa: BLE001
        return "YubiKey"


def _applications(info) -> dict[str, dict[str, bool]]:
    out: dict[str, dict[str, bool]] = {}
    for transport in (TRANSPORT.USB, TRANSPORT.NFC):
        supported = info.supported_capabilities.get(transport, 0)
        if not supported:
            continue
        enabled = info.config.enabled_capabilities.get(transport, 0)
        out[transport.name] = {name: bool(enabled & cap) for cap, name in _APP_NAMES if supported & cap}
    return out


def _pcsc_available() -> bool:
    """True when pcscd answers. ykman swallows this error, so check directly."""
    try:
        from smartcard.System import readers

        readers()
        return True
    except Exception as e:  # noqa: BLE001
        return "Service not available" not in str(e) and "0x8010001D" not in str(e)


def _yubikey_busy() -> bool:
    """True when a YubiKey reader exists but the card is locked by another client
    (typically Yubico Authenticator or GnuPG's scdaemon)."""
    try:
        from smartcard.System import readers

        for r in readers():
            if "yubi" not in str(r).lower():
                continue
            conn = r.createConnection()
            try:
                conn.connect()
                conn.disconnect()
            except Exception as e:  # noqa: BLE001
                if "0x8010000B" in str(e) or "Sharing violation" in str(e):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _sort_key(c: Credential):
    return ((c.issuer or c.name).lower(), c.name.lower())


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
