"""QR code scanning from a screenshot, off the main thread.

Today the only backend is grim (wlroots/Hyprland screenshots) piped into
zbarimg. The blocking work runs on a worker thread and the result is handed
back on the GTK main loop, so a slow decode or a hung screenshot tool never
freezes the window.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from collections.abc import Callable

from gi.repository import GLib

log = logging.getLogger(__name__)

SCREENSHOT_TIMEOUT = 10
DECODE_TIMEOUT = 20
ZBAR_NO_SYMBOL = 4  # zbarimg exit status when the image contains no barcode

Done = Callable[[list[str], str | None], None]  # (otpauth URIs found, error message)


def tools_available() -> bool:
    return bool(shutil.which("grim") and shutil.which("zbarimg"))


def scan_screen(done: Done) -> None:
    """Take a screenshot, decode it, call `done(uris, error)` on the main loop."""

    def work() -> None:
        try:
            uris, error = _run_scan(shutil.which("grim"), shutil.which("zbarimg"))
        except Exception as e:
            log.debug("scan failed", exc_info=True)
            uris, error = [], str(e) or e.__class__.__name__
        GLib.idle_add(lambda: (done(uris, error), False)[1])

    threading.Thread(target=work, name="qr-scan", daemon=True).start()


def _run_scan(grim: str | None, zbarimg: str | None) -> tuple[list[str], str | None]:
    if not grim or not zbarimg:
        return [], "grim and zbarimg are required for scanning"
    shot = subprocess.run([grim, "-"], capture_output=True, timeout=SCREENSHOT_TIMEOUT, check=False)
    if shot.returncode != 0:
        return [], f"Screenshot failed: {_stderr(shot) or f'grim exited with {shot.returncode}'}"
    dec = subprocess.run(
        [zbarimg, "-q", "--raw", "-"],
        input=shot.stdout,
        capture_output=True,
        timeout=DECODE_TIMEOUT,
        check=False,
    )
    if dec.returncode not in (0, ZBAR_NO_SYMBOL):
        return [], f"Decoding failed: {_stderr(dec) or f'zbarimg exited with {dec.returncode}'}"
    return parse_zbar(dec.stdout.decode(errors="replace")), None


def parse_zbar(out: str) -> list[str]:
    """otpauth URIs from zbarimg --raw output, in order, without duplicates."""
    seen: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("otpauth://") and s not in seen:
            seen.append(s)
    return seen


def _stderr(p: subprocess.CompletedProcess) -> str:
    return p.stderr.decode(errors="replace").strip().splitlines()[-1] if p.stderr.strip() else ""
