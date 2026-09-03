"""Aegis-format icon packs: a zip with pack.json listing icons and the issuers
they match. Both aegis-icons and Yubico's own pack use this format."""

from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class IconPack:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).stem
        self._zip = zipfile.ZipFile(path)
        self._by_issuer: dict[str, str] = {}
        self._cache: dict[str, Gdk.Texture | None] = {}
        meta_name = next((n for n in self._zip.namelist() if n.endswith("pack.json")), None)
        if meta_name is None:
            raise ValueError("not an icon pack: pack.json missing")
        base = meta_name[: -len("pack.json")]
        meta = json.loads(self._zip.read(meta_name))
        self.name = meta.get("name", self.name)
        for icon in meta.get("icons", []):
            fn = base + icon["filename"]
            for issuer in icon.get("issuer", []):
                self._by_issuer.setdefault(_norm(issuer), fn)
        log.debug("icon pack %s: %d issuers", self.name, len(self._by_issuer))

    def lookup(self, issuer: str | None, name: str = "") -> Gdk.Texture | None:
        key = self._match(issuer, name)
        if key is None:
            return None
        if key not in self._cache:
            self._cache[key] = self._load(self._by_issuer[key])
        return self._cache[key]

    def _match(self, issuer: str | None, name: str) -> str | None:
        candidates = []
        if issuer:
            candidates.append(_norm(issuer))
            # "Amazon JP" -> "amazon", "GitHub (work)" -> "github"
            candidates.append(_norm(re.split(r"[\s(:/-]", issuer.strip(), 1)[0]))
        if name and "@" in name:
            domain = name.rsplit("@", 1)[1]
            candidates.append(_norm(domain))
            candidates.append(_norm(domain.split(".")[0]))
        for c in candidates:
            if c and c in self._by_issuer:
                return c
        return None

    def _load(self, member: str, size: int = 64) -> Gdk.Texture | None:
        try:
            data = self._zip.read(member)
            loader = GdkPixbuf.PixbufLoader()
            loader.connect("size-prepared", lambda ld, w, h: ld.set_size(size, int(size * h / max(w, 1))))
            loader.write(GLib.Bytes.new(data).get_data())
            loader.close()
            pixbuf = loader.get_pixbuf()
            return Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf else None
        except Exception as e:  # noqa: BLE001
            log.debug("icon %s failed: %s", member, e)
            return None


def load_pack(path: str | None) -> IconPack | None:
    if not path:
        return None
    try:
        return IconPack(path)
    except Exception as e:  # noqa: BLE001
        log.warning("icon pack %s: %s", path, e)
        return None
