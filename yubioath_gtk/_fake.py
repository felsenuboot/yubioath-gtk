"""Hardware-free backend for UI work. Enable with YUBIOATH_FAKE=1."""

from __future__ import annotations

import hashlib
import hmac
import struct
import time
from dataclasses import replace

from gi.repository import GLib

from yubikit.oath import OATH_TYPE, Code, Credential

from .backend import Backend, DeviceState, DeviceSummary

_SEED = [
    ("GitHub", "alice@example.org", False, OATH_TYPE.TOTP),
    ("Google", "alice.example@gmail.com", False, OATH_TYPE.TOTP),
    ("Hetzner Cloud", "alice", True, OATH_TYPE.TOTP),
    ("Tailscale", "alice", False, OATH_TYPE.TOTP),
    (None, "legacy-vpn", False, OATH_TYPE.HOTP),
    ("Proton", "alice@example.com", True, OATH_TYPE.TOTP),
]


def _totp(key: bytes, counter: int) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


class FakeBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        self._creds = [
            Credential("fake", f"{iss}:{name}".encode() if iss else name.encode(), iss, name, t, 30, touch)
            for iss, name, touch, t in _SEED
        ]
        self._counter = 0

    def start(self) -> None:
        self._state = DeviceState(
            name="YubiKey 5C NFC",
            serial=12345678,
            fingerprint="fake1",
            version="5.7.1",
            form_factor="USB-C Keychain",
            has_password=True,
            applications={
                "USB": {
                    "Yubico OTP": True,
                    "FIDO U2F": True,
                    "FIDO2": True,
                    "OATH": True,
                    "PIV": True,
                    "OpenPGP": False,
                },
                "NFC": {
                    "Yubico OTP": True,
                    "FIDO U2F": True,
                    "FIDO2": True,
                    "OATH": True,
                    "PIV": False,
                    "OpenPGP": False,
                },
            },
        )
        devices = [
            DeviceSummary("fake1", "YubiKey 5C NFC", 12345678),
            DeviceSummary("fake2", "YubiKey 5 Nano", 87654321),
        ]
        GLib.timeout_add(
            600,
            lambda: (
                self.on_service(True),
                self.on_devices(devices, "fake1"),
                self.on_device(self._state),
                self.refresh(),
                False,
            )[-1],
        )

    def stop(self) -> None:
        pass

    def _code(self, cred: Credential, now: float) -> Code:
        start = int(now) // 30 * 30
        return Code(_totp(cred.id, start // 30), start, start + 30)

    def refresh(self) -> None:
        now = time.time()
        codes = {
            c.id: self._code(c, now)
            for c in self._creds
            if c.oath_type == OATH_TYPE.TOTP and not c.touch_required
        }
        GLib.idle_add(lambda: (self.on_accounts(list(self._creds), codes), False)[1])

    def calculate(self, cred: Credential) -> None:
        if cred.oath_type == OATH_TYPE.HOTP:
            self._counter += 1
            code = Code(_totp(cred.id, self._counter), 0, 2**32)
        else:
            code = self._code(cred, time.time())
        GLib.timeout_add(1500 if cred.touch_required else 200, lambda: (self.on_code(cred, code), False)[1])

    def unlock(self, password: str, remember: bool) -> None:
        self.refresh()

    def forget_password(self) -> None:
        GLib.idle_add(
            lambda: (self.on_info("Saved password removed; the key will ask for it again"), False)[1]
        )

    def add(self, data, touch, done) -> None:
        cid = f"{data.issuer}:{data.name}".encode() if data.issuer else data.name.encode()
        self._creds.append(
            Credential("fake", cid, data.issuer, data.name, data.oath_type, data.period, touch)
        )
        GLib.idle_add(lambda: (done(None), False)[1])
        self.refresh()

    def rename(self, cred, name, issuer) -> None:
        i = self._creds.index(cred)
        cid = f"{issuer}:{name}".encode() if issuer else name.encode()
        self._creds[i] = Credential(
            "fake", cid, issuer, name, cred.oath_type, cred.period, cred.touch_required
        )
        self.refresh()

    def delete(self, cred) -> None:
        self._creds.remove(cred)
        self.refresh()

    def select_device(self, fingerprint: str) -> None:
        GLib.idle_add(lambda: (self.on_error(f"Selected {fingerprint}"), False)[1])

    def set_password(self, password, remember, done) -> None:
        self._state = replace(self._state, has_password=True)
        GLib.timeout_add(300, lambda: (self.on_device(self._state), done(None), False)[-1])

    def remove_password(self, done) -> None:
        self._state = replace(self._state, has_password=False)
        GLib.timeout_add(300, lambda: (self.on_device(self._state), done(None), False)[-1])

    def reset_oath(self, done) -> None:
        self._creds.clear()
        self._state = replace(self._state, has_password=False)
        GLib.timeout_add(300, lambda: (self.on_device(self._state), done(None), self.refresh(), False)[-1])
