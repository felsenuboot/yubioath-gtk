"""Clipboard writes with an optional timed clear.

GDK's clipboard is used by default. When the window has no focus (a copy from
the tray icon, typically), Wayland compositors that validate the input serial
silently drop the write, so `external=True` prefers wl-copy, which uses the
data-control protocol and needs no focus. Hyprland accepts the GDK write
either way; sway and other wlroots compositors need the fallback.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil

from gi.repository import Gdk, Gio, GLib

log = logging.getLogger(__name__)


class Clipboard:
    def __init__(self) -> None:
        self._clear_source: int | None = None
        self._wl_copy = shutil.which("wl-copy") if os.environ.get("WAYLAND_DISPLAY") else None
        self._wl_paste = shutil.which("wl-paste") if self._wl_copy else None

    def copy(self, text: str, clear_after: int = 0, external: bool = False) -> None:
        self._cancel_clear()
        if external and self._wl_copy:
            self._copy_external(text, clear_after)
        else:
            self._copy_gdk(text, clear_after)

    # -- GDK -----------------------------------------------------------------

    def _copy_gdk(self, text: str, clear_after: int) -> None:
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        if clear_after > 0:

            def clear() -> bool:
                self._clear_source = None
                if clipboard.is_local():  # still ours, nobody else copied since
                    clipboard.read_text_async(None, lambda c, r: _clear_if(c, r, text))
                return False

            self._clear_source = GLib.timeout_add_seconds(clear_after, clear)

    # -- wl-copy -------------------------------------------------------------

    def _copy_external(self, text: str, clear_after: int) -> None:
        try:
            proc = Gio.Subprocess.new(
                [self._wl_copy], Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            )
        except GLib.Error as e:
            log.warning("wl-copy failed to start: %s", e)
            self._copy_gdk(text, clear_after)
            return

        def done(p, res):
            try:
                p.communicate_utf8_finish(res)
                ok = p.get_successful()
            except GLib.Error:
                ok = False
            if not ok:  # compositor without data-control support, most likely
                log.debug("wl-copy failed, using GDK clipboard")
                self._copy_gdk(text, clear_after)
            elif clear_after > 0:
                self._clear_source = GLib.timeout_add_seconds(clear_after, lambda: self._clear_external(text))

        proc.communicate_utf8_async(text, None, done)

    def _clear_external(self, text: str) -> bool:
        self._clear_source = None
        if not self._wl_paste:
            return False
        try:
            proc = Gio.Subprocess.new(
                [self._wl_paste, "--no-newline"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            return False

        def done(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error:
                return
            if out == text:
                with contextlib.suppress(GLib.Error):
                    Gio.Subprocess.new([self._wl_copy, "--clear"], Gio.SubprocessFlags.STDERR_SILENCE)

        proc.communicate_utf8_async(None, None, done)
        return False

    def _cancel_clear(self) -> None:
        if self._clear_source:
            GLib.source_remove(self._clear_source)
            self._clear_source = None


def _clear_if(clipboard: Gdk.Clipboard, result, value: str) -> None:
    try:
        text = clipboard.read_text_finish(result)
    except Exception:  # noqa: BLE001
        return
    if text == value:
        clipboard.set("")
