from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .config import config  # noqa: E402

THEMES = ["system", "light", "dark"]


class PreferencesDialog(Adw.PreferencesDialog):
    """Backed by the JSON config; `on_change(key)` tells the window what changed."""

    def __init__(self, on_change: Callable[[str], None]) -> None:
        super().__init__(title="Preferences")
        self._on_change = on_change
        page = Adw.PreferencesPage()
        self.add(page)

        g = Adw.PreferencesGroup(title="Appearance")
        theme = Adw.ComboRow(title="Theme", model=Gtk.StringList.new(["Follow system", "Light", "Dark"]))
        theme.set_selected(THEMES.index(config.get("theme")) if config.get("theme") in THEMES else 0)
        theme.connect("notify::selected", lambda r, _p: self._set("theme", THEMES[r.get_selected()]))
        g.add(theme)

        self.pack_row = Adw.ActionRow(title="Icon pack", activatable=True)
        self.pack_row.set_subtitle_selectable(False)
        pick = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Choose file")
        pick.add_css_class("flat")
        pick.connect("clicked", self._pick_pack)
        self.clear_btn = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
        self.clear_btn.add_css_class("flat")
        self.clear_btn.connect("clicked", lambda *_: self._set("icon_pack", None))
        self.pack_row.add_suffix(self.clear_btn)
        self.pack_row.add_suffix(pick)
        self.pack_row.connect("activated", self._pick_pack)
        g.add(self.pack_row)
        hint = Adw.ActionRow(
            title="Aegis-format packs work, e.g. aegis-icons",
            subtitle="github.com/aegis-icons/aegis-icons/releases — download the .zip and pick it above",
        )
        hint.add_css_class("dim-label")
        g.add(hint)
        page.add(g)

        g = Adw.PreferencesGroup(title="Behaviour")
        hide = Adw.SwitchRow(title="Hide codes until clicked", subtitle="Click a row to reveal and copy its code")
        hide.set_active(bool(config.get("hide_codes")))
        hide.connect("notify::active", lambda r, _p: self._set("hide_codes", r.get_active()))
        g.add(hide)
        clear = Adw.SpinRow.new_with_range(0, 300, 5)
        clear.set_title("Clear clipboard after")
        clear.set_subtitle("Seconds; 0 keeps the code in the clipboard")
        clear.set_value(int(config.get("clipboard_clear") or 0))
        clear.connect("notify::value", lambda r, _p: self._set("clipboard_clear", int(r.get_value())))
        g.add(clear)
        page.add(g)

        self._update_pack_row()

    def _set(self, key: str, value) -> None:
        config.set(key, value)
        if key == "icon_pack":
            self._update_pack_row()
        self._on_change(key)

    def _update_pack_row(self) -> None:
        path = config.get("icon_pack")
        self.pack_row.set_subtitle(path or "None (coloured initials)")
        self.clear_btn.set_visible(bool(path))

    def _pick_pack(self, *_) -> None:
        dlg = Gtk.FileDialog(title="Choose icon pack")
        f = Gtk.FileFilter()
        f.set_name("Icon pack (zip)")
        f.add_suffix("zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)
        dlg.set_default_filter(f)

        def done(d, res):
            try:
                file = d.open_finish(res)
            except Exception:  # noqa: BLE001  (cancelled)
                return
            if file:
                self._set("icon_pack", file.get_path())

        dlg.open(self.get_root(), None, done)
