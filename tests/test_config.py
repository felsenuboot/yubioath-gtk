import json

from yubioath_gtk.config import DEFAULTS, Config


def test_defaults_when_file_missing(tmp_path):
    c = Config(tmp_path / "config.json")
    assert c.get("theme") == "system"
    assert c.get("tray_icon") is True
    assert c.get("nonexistent") is None


def test_set_persists_and_is_atomic(tmp_path):
    path = tmp_path / "cfg" / "config.json"
    c = Config(path)
    c.set("theme", "dark")
    assert json.loads(path.read_text())["theme"] == "dark"
    assert not path.with_suffix(".tmp").exists()
    assert Config(path).get("theme") == "dark"


def test_set_same_value_does_not_write(tmp_path):
    path = tmp_path / "config.json"
    c = Config(path)
    c.set("theme", DEFAULTS["theme"])
    assert not path.exists()


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    c = Config(path)
    assert c.get("theme") == "system"


def test_unknown_keys_survive_round_trip(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"future_option": 42}))
    c = Config(path)
    c.set("theme", "light")
    assert json.loads(path.read_text())["future_option"] == 42


def test_favorites(tmp_path):
    c = Config(tmp_path / "config.json")
    cid = b"GitHub:alice"
    assert not c.is_favorite("dev1", cid)
    c.set_favorite("dev1", cid, True)
    assert c.is_favorite("dev1", cid)
    assert not c.is_favorite("dev2", cid)
    c.set_favorite("dev1", cid, True)  # idempotent
    assert c.get("favorites")["dev1"] == [cid.hex()]
    c.set_favorite("dev1", cid, False)
    assert not c.is_favorite("dev1", cid)
