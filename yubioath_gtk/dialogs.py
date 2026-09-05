from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .backend import DeviceState  # noqa: E402


class DeviceInfoDialog(Adw.Dialog):
    def __init__(self, state: DeviceState) -> None:
        super().__init__(title="Device", content_width=420)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        view.set_content(page)
        self.set_child(view)

        g = Adw.PreferencesGroup(title=state.name)
        rows = [
            ("Serial number", str(state.serial) if state.serial else "Not available"),
            ("Firmware", state.version or "Unknown"),
            ("Form factor", state.form_factor or "Unknown"),
        ]
        if state.is_fips:
            rows.append(("FIPS", "Yes"))
        if state.is_sky:
            rows.append(("Security Key series", "Yes"))
        rows.append(("OATH password", "Set" if state.has_password else "Not set"))
        for title, value in rows:
            r = Adw.ActionRow(title=title, subtitle=value)
            r.add_css_class("property")
            r.set_subtitle_selectable(True)
            g.add(r)
        page.add(g)

        for transport, apps in (state.applications or {}).items():
            g = Adw.PreferencesGroup(title=f"Applications over {transport}")
            for name, enabled in apps.items():
                r = Adw.ActionRow(title=name)
                icon = Gtk.Image.new_from_icon_name(
                    "emblem-ok-symbolic" if enabled else "window-close-symbolic"
                )
                icon.add_css_class("success" if enabled else "dim-label")
                r.add_suffix(icon)
                if not enabled:
                    r.add_css_class("dim-label")
                g.add(r)
            page.add(g)


class PasswordDialog(Adw.Dialog):
    """Set or change the OATH password, or remove it."""

    def __init__(
        self,
        has_password: bool,
        on_set: Callable[[str, bool, Callable[[str | None], None]], None],
        on_remove: Callable[[Callable[[str | None], None]], None],
    ) -> None:
        super().__init__(title="Change password" if has_password else "Set password", content_width=420)
        self._on_set = on_set
        self._on_remove = on_remove

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.set_sensitive(False)
        self.save_btn.connect("clicked", self._save)
        header.pack_end(self.save_btn)
        view.add_top_bar(header)
        self.toasts = Adw.ToastOverlay()
        page = Adw.PreferencesPage()
        self.toasts.set_child(page)
        view.set_content(self.toasts)
        self.set_child(view)

        g = Adw.PreferencesGroup(
            description="Protects the accounts on this YubiKey. You will be asked for it "
            "whenever the key is used, on every computer."
        )
        self.pw = Adw.PasswordEntryRow(title="New password")
        self.confirm = Adw.PasswordEntryRow(title="Confirm password")
        self.remember = Adw.SwitchRow(
            title="Remember on this computer", subtitle="Stored in the system keyring"
        )
        self.remember.set_active(True)
        for r in (self.pw, self.confirm):
            r.connect("changed", lambda *_: self._validate())
            g.add(r)
        self.confirm.connect("entry-activated", self._save)
        g.add(self.remember)
        page.add(g)

        if has_password:
            g = Adw.PreferencesGroup()
            remove = Adw.ButtonRow(title="Remove password")
            remove.add_css_class("destructive-action")
            remove.connect("activated", self._remove)
            g.add(remove)
            page.add(g)

        self.connect("map", lambda *_: self.pw.grab_focus())

    def _validate(self) -> None:
        a, b = self.pw.get_text(), self.confirm.get_text()
        ok = bool(a) and a == b
        self.save_btn.set_sensitive(ok)
        if b and a != b:
            self.confirm.add_css_class("error")
        else:
            self.confirm.remove_css_class("error")

    def _save(self, *_) -> None:
        if not self.save_btn.get_sensitive():
            return
        self.set_sensitive(False)
        self._on_set(self.pw.get_text(), self.remember.get_active(), self._done)

    def _remove(self, *_) -> None:
        self.set_sensitive(False)
        self._on_remove(self._done)

    def _done(self, err: str | None) -> None:
        self.set_sensitive(True)
        if err:
            self.toasts.add_toast(Adw.Toast(title=err, timeout=4))
        else:
            self.close()
