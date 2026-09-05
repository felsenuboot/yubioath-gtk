import pytest
from yubikit.oath import CredentialData

from yubioath_gtk.add_dialog import unsupported_reason

SECRET = "JBSWY3DPEHPK3PXP"  # noqa: S105  (the RFC 6238 test vector, not a secret)


def uri(**q):
    query = "&".join(f"{k}={v}" for k, v in {"secret": SECRET, **q}.items())
    return f"otpauth://totp/Example:alice?{query}"


@pytest.mark.parametrize(
    "extra", [{}, {"digits": 7}, {"digits": 8}, {"period": 60}, {"period": 15}, {"algorithm": "SHA256"}]
)
def test_supported_variants(extra):
    assert unsupported_reason(CredentialData.parse_uri(uri(**extra))) is None


def test_unsupported_digits():
    assert "digits" in unsupported_reason(CredentialData.parse_uri(uri(digits=5)))


def test_unsupported_period():
    assert "Period 0" in unsupported_reason(CredentialData.parse_uri(uri(period=0)))
    assert "Period 7200" in unsupported_reason(CredentialData.parse_uri(uri(period=7200)))


def test_hotp_ignores_period():
    d = CredentialData.parse_uri(f"otpauth://hotp/Example:alice?secret={SECRET}&counter=5")
    assert unsupported_reason(d) is None
