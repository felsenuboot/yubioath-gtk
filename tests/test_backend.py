from yubikit.core import ApplicationNotAvailableError
from yubikit.core.smartcard import SW, ApduError
from yubikit.oath import OATH_TYPE, Credential

from yubioath_gtk.backend import Backend, BackendError, DeviceSummary, TouchTimeout, _sort_key


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
