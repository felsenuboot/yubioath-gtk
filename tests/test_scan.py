import os
import stat

import pytest

from yubioath_gtk.scan import _run_scan, parse_zbar


def test_parse_zbar_filters_and_dedupes():
    out = "hello\notpauth://totp/A?secret=X\n\n  otpauth://totp/A?secret=X \notpauth://hotp/B?secret=Y\n"
    assert parse_zbar(out) == ["otpauth://totp/A?secret=X", "otpauth://hotp/B?secret=Y"]
    assert parse_zbar("") == []


def fake_tool(path, script):
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def grim(tmp_path):
    return fake_tool(tmp_path / "grim", "printf 'PNG'\n")


def test_run_scan_finds_uri(tmp_path, grim):
    zbar = fake_tool(tmp_path / "zbarimg", "cat >/dev/null; echo 'otpauth://totp/X?secret=ABC'\n")
    assert _run_scan(grim, zbar) == (["otpauth://totp/X?secret=ABC"], None)


def test_run_scan_no_symbol_is_not_an_error(tmp_path, grim):
    zbar = fake_tool(tmp_path / "zbarimg", "cat >/dev/null; exit 4\n")
    assert _run_scan(grim, zbar) == ([], None)


def test_run_scan_reports_decoder_failure(tmp_path, grim):
    zbar = fake_tool(tmp_path / "zbarimg", "cat >/dev/null; echo 'boom' >&2; exit 1\n")
    uris, err = _run_scan(grim, zbar)
    assert uris == []
    assert err == "Decoding failed: boom"


def test_run_scan_reports_screenshot_failure(tmp_path):
    grim = fake_tool(tmp_path / "grim", "echo 'compositor doesn'\\''t support wlr-screencopy' >&2; exit 1\n")
    zbar = fake_tool(tmp_path / "zbarimg", "exit 0\n")
    uris, err = _run_scan(grim, zbar)
    assert uris == []
    assert err.startswith("Screenshot failed: compositor")


def test_run_scan_without_tools():
    assert _run_scan(None, "/bin/true") == ([], "grim and zbarimg are required for scanning")
    assert os.path.exists("/bin/true")
