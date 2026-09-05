"""Tiny JSON config store in ~/.config/yubioath-gtk/config.json."""

from __future__ import annotations

import json
import logging
import os
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


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(GLib.get_user_config_dir()) / "yubioath-gtk" / "config.json"
        self._data: dict[str, Any] = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("could not read %s: %s", self.path, e)

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            log.warning("could not write %s: %s", self.path, e)

    # -- favorites -------------------------------------------------------

    def is_favorite(self, device_id: str, cred_id: bytes) -> bool:
        return cred_id.hex() in self._data.get("favorites", {}).get(device_id, [])

    def set_favorite(self, device_id: str, cred_id: bytes, fav: bool) -> None:
        favs = dict(self._data.get("favorites", {}))
        ids = list(favs.get(device_id, []))
        h = cred_id.hex()
        if fav and h not in ids:
            ids.append(h)
        elif not fav and h in ids:
            ids.remove(h)
        favs[device_id] = ids
        self._data["favorites"] = favs
        self.save()


config = Config()
