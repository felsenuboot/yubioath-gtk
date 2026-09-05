"""Tiny JSON config store in ~/.config/yubioath-gtk/config.json.

Writes are thread-safe: the backend worker records the last used serial while
the main thread saves preferences and favourites. Changes are coalesced and
written a moment later from the main loop; `flush()` writes immediately.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from gi.repository import GLib

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "theme": "system",  # system | light | dark
    "hide_codes": False,
    "clipboard_clear": 0,  # seconds, 0 = never
    "icon_pack": None,  # path to an Aegis icon pack zip
    "favorites": {},  # device_id -> [credential id hex, ...]
    "last_serial": None,
    "tray_icon": True,  # StatusNotifierItem in the bar
    "close_to_tray": False,  # closing the window keeps the app running
    "start_hidden": False,  # launch with only the tray icon
}

SAVE_DELAY_MS = 250


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(GLib.get_user_config_dir()) / "yubioath-gtk" / "config.json"
        self._lock = threading.RLock()
        self._dirty = False
        self._save_source: int | None = None
        self._data: dict[str, Any] = dict(DEFAULTS)
        try:
            with self.path.open(encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("could not read %s: %s", self.path, e)

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self._schedule_save()

    # -- persistence -----------------------------------------------------

    def _schedule_save(self) -> None:
        """Called with the lock held. Coalesces bursts (spin rows, favourites)
        into one write, issued from the main loop."""
        self._dirty = True
        if self._save_source is None:
            self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timeout)

    def _on_save_timeout(self) -> bool:
        with self._lock:
            self._save_source = None
        self.flush()
        return False

    def flush(self) -> None:
        """Write now if anything changed. Safe from any thread."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            if self._save_source is not None:
                GLib.source_remove(self._save_source)
                self._save_source = None
            # Write while still holding the lock: two flushes racing outside it
            # could otherwise land an older snapshot last.
            self._write(json.dumps(self._data, indent=2))

    def _write(self, text: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # A private temp file per write, so two writers can never share one.
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except Exception as e:  # noqa: BLE001
            log.warning("could not write %s: %s", self.path, e)

    def save(self) -> None:  # kept for callers that want an immediate write
        with self._lock:
            self._dirty = True
        self.flush()

    # -- favorites -------------------------------------------------------

    def is_favorite(self, device_id: str, cred_id: bytes) -> bool:
        with self._lock:
            return cred_id.hex() in self._data.get("favorites", {}).get(device_id, [])

    def set_favorite(self, device_id: str, cred_id: bytes, fav: bool) -> None:
        with self._lock:
            favs = {k: list(v) for k, v in self._data.get("favorites", {}).items()}
            ids = favs.get(device_id, [])
            h = cred_id.hex()
            if fav and h not in ids:
                ids.append(h)
            elif not fav and h in ids:
                ids.remove(h)
            if ids:
                favs[device_id] = ids
            else:
                favs.pop(device_id, None)  # no empty lists lingering in the file
            self._data["favorites"] = favs
            self._schedule_save()


config = Config()
