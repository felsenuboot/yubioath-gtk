import pytest

from yubioath_gtk import keystore


def test_memory_store_round_trip():
    s = keystore.MemoryStore()
    assert s.available
    assert s.lookup("d") is None
    s.store("d", b"\x01\x02")
    assert s.lookup("d") == b"\x01\x02"
    s.clear("d")
    s.clear("d")  # idempotent
    assert s.lookup("d") is None


def test_null_store_is_inert():
    s = keystore.NullStore()
    assert not s.available
    s.store("d", b"x")
    assert s.lookup("d") is None
    s.clear("d")


def test_default_store_never_raises():
    s = keystore.default_store()
    assert isinstance(s.available, bool)
    assert s.name in ("libsecret", "keyring", "none")


def test_keyring_store_skipped_without_package():
    pytest.importorskip("keyring")
    try:
        s = keystore.KeyringStore()
    except RuntimeError as e:  # no usable backend on this machine
        pytest.skip(str(e))
    assert s.available
