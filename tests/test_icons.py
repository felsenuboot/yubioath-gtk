import io
import json
import zipfile

import pytest

from yubioath_gtk.icons import IconPack, _norm, load_pack


def _png() -> bytes:
    """A 2x2 PNG produced by GdkPixbuf itself, so decoding it cannot fail."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
    pb.fill(0x3584E4FF)
    ok, data = pb.save_to_bufferv("png", [], [])
    assert ok
    return bytes(data)


PNG = _png()


def make_pack(path, icons, prefix="pack/"):
    meta = {
        "name": "Test Pack",
        "icons": [{"filename": fn, "issuer": issuers} for fn, issuers in icons.items()],
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(prefix + "pack.json", json.dumps(meta))
        for fn in icons:
            z.writestr(prefix + fn, PNG)
    return path


@pytest.fixture
def pack(tmp_path):
    p = make_pack(
        tmp_path / "icons.zip",
        {
            "github.png": ["GitHub"],
            "amazon.png": ["Amazon", "AWS"],
            "google.png": ["Google", "gmail.com"],
            "proton.png": ["Proton Mail", "ProtonMail", "Proton"],
        },
    )
    return IconPack(str(p))


@pytest.mark.parametrize(
    ("raw", "norm"),
    [("GitHub", "github"), ("Amazon JP", "amazonjp"), ("Proton Mail", "protonmail"), ("  x-y_z ", "xyz")],
)
def test_norm(raw, norm):
    assert _norm(raw) == norm


def test_pack_reads_metadata(pack):
    assert pack.name == "Test Pack"
    assert pack._by_issuer["aws"] == "pack/amazon.png"


@pytest.mark.parametrize(
    ("issuer", "name", "key"),
    [
        ("GitHub", "alice", "github"),
        ("github", "", "github"),
        ("Amazon JP", "alice", "amazon"),  # first word fallback
        ("GitHub (work)", "alice", "github"),
        ("AWS", "root", "aws"),
        (None, "alice@gmail.com", "gmailcom"),  # whole e-mail domain first
        (None, "alice@mail.google.com", "google"),  # then each label except the TLD
        (None, "alice@proton.me", "proton"),
        ("Unknown Corp", "bob", None),
        (None, "bob", None),
    ],
)
def test_match(pack, issuer, name, key):
    assert pack._match(issuer, name) == key


def test_lookup_returns_texture_and_caches(pack):
    t1 = pack.lookup("GitHub", "alice")
    t2 = pack.lookup("github", "bob")
    assert t1 is not None
    assert t1 is t2
    assert pack.lookup("Nobody", "x") is None


def test_load_pack_tolerates_bad_input(tmp_path):
    assert load_pack(None) is None
    assert load_pack(str(tmp_path / "missing.zip")) is None
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "no pack.json here")
    assert load_pack(str(bad)) is None


def test_pack_json_at_root(tmp_path):
    p = make_pack(tmp_path / "root.zip", {"github.png": ["GitHub"]}, prefix="")
    assert IconPack(str(p))._by_issuer["github"] == "github.png"


def test_png_fixture_is_valid():
    # Guards the fixture itself: zipfile must read back what we wrote.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.png", PNG)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
        assert z.read("a.png") == PNG
