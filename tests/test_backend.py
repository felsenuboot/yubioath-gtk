from yubikit.core import ApplicationNotAvailableError
from yubikit.core.smartcard import SW, ApduError
from yubikit.oath import OATH_TYPE, Credential

from yubioath_gtk.backend import Backend, BackendError, DeviceState, DeviceSummary, TouchTimeout, _sort_key


def cred(issuer, name):
    return Credential(
        "dev",
        f"{issuer}:{name}".encode() if issuer else name.encode(),
        issuer,
        name,
        OATH_TYPE.TOTP,
        30,
        False,
    )


def test_sort_key_is_case_insensitive_and_uses_name_without_issuer():
    creds = [cred("zeta", "a"), cred(None, "Beta"), cred("Alpha", "z"), cred("alpha", "b")]
    ordered = sorted(creds, key=_sort_key)
    assert [(c.issuer, c.name) for c in ordered] == [
        ("alpha", "b"),
        ("Alpha", "z"),
        (None, "Beta"),
        ("zeta", "a"),
    ]


def test_describe_maps_known_errors():
    d = Backend._describe
    assert d(TouchTimeout("x")) == "Timed out waiting for touch"
    assert d(ApplicationNotAvailableError()) == "OATH is not enabled on this YubiKey"
    assert d(ApduError(b"", SW.NO_SPACE)) == "No space left on the YubiKey"
    assert d(ApduError(b"", SW.SECURITY_CONDITION_NOT_SATISFIED)) == "Authentication required"
    assert d(ApduError(b"", 0x6A88)) == "YubiKey error (SW=6a88)"
    assert d(BackendError("custom")) == "custom"
    assert d(RuntimeError("Card is not present")) == "YubiKey was removed"
    assert d(RuntimeError("")) == "RuntimeError"


def test_device_summary_label():
    assert DeviceSummary("fp", "YubiKey 5C", 123).label == "YubiKey 5C · 123"
    assert DeviceSummary("fp", "YubiKey NEO", None).label == "YubiKey NEO"


def test_device_state_is_immutable():
    import dataclasses

    import pytest

    s = DeviceState(name="k", serial=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.locked = True  # type: ignore[misc]


def test_update_state_emits_a_new_snapshot_only_on_change():
    from gi.repository import GLib

    b = Backend()
    seen = []
    b.on_device = seen.append
    original = DeviceState(name="k", serial=1)
    b._state = original
    b._set_locked("dev", problem="typed")
    b._update_state(locked=True)  # no change: no emission
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)
    assert len(seen) == 1
    assert seen[0].locked and seen[0].device_id == "dev" and seen[0].auth_failure == "typed"
    assert original.locked is False  # the old snapshot is untouched
    assert b._state is seen[0]
