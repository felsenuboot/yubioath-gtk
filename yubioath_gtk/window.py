from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from yubikit.oath import OATH_TYPE, Code, Credential, CredentialData  # noqa: E402

from .add_dialog import AddAccountDialog  # noqa: E402
from .backend import Backend, DeviceState  # noqa: E402
from .clipboard import Clipboard  # noqa: E402
from .config import config  # noqa: E402
from .dialogs import DeviceInfoDialog, PasswordDialog  # noqa: E402
from .icons import load_pack  # noqa: E402
from .prefs import PreferencesDialog  # noqa: E402
from .sysinfo import pcsc_install_hint  # noqa: E402
from .tray import MenuItem  # noqa: E402
from .widgets import AccountRow  # noqa: E402

import logging  # noqa: E402

log = logging.getLogger(__name__)


class MainWindow(Adw.ApplicationWindow):
    __gsignals__ = {
        # Anything the tray menu shows has changed: accounts, device, visibility.
        "accounts-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, app: Adw.Application, backend: Backend) -> None:
        super().__init__(application=app, title="YubiOath")
        self.set_default_size(400, 620)
        self.set_size_request(320, 300)
        self.backend = backend
        self.rows: dict[bytes, AccountRow] = {}
        self._refresh_source: int | None = None
        self._tick_source: int | None = None
        self._creds: list[Credential] = []
        self._state: DeviceState | None = None  # last snapshot from the backend
        self._service_ok = True
        self._png_cache: dict[int, bytes] = {}
        self.icon_pack = load_pack(config.get("icon_pack"))
        self.clipboard = Clipboard()
        self._apply_tray_prefs()
        self.connect("notify::visible", self._visible_changed)

        backend.on_device = self._on_device
        backend.on_accounts = self._on_accounts
        backend.on_code = self._on_code
        backend.on_error = self._on_error
        backend.on_info = self._toast
        backend.on_service = self._on_service
        backend.on_devices = self._on_devices

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # -- header --------------------------------------------------------
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="YubiOath", subtitle="")
        header.set_title_widget(self.title_widget)
        self.search_btn = Gtk.ToggleButton(icon_name="edit-find-symbolic", tooltip_text="Search (Ctrl+F)")
        header.pack_start(self.search_btn)
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._menu_model())
        header.pack_end(menu)
        self.device_btn = Gtk.MenuButton(
            icon_name="drive-removable-media-symbolic", tooltip_text="Switch YubiKey"
        )
        self.device_btn.set_visible(False)
        header.pack_start(self.device_btn)
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add account (Ctrl+N)")
        add_btn.set_action_name("win.add")
        header.pack_end(add_btn)
        view.add_top_bar(header)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search accounts")
        self.search_entry.set_hexpand(True)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_btn.bind_property(
            "active",
            self.search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self.search_entry.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        self.search_entry.connect("stop-search", lambda *_: self.search_bar.set_search_mode(False))
        self.search_entry.connect("activate", self._activate_first_visible)
        view.add_top_bar(self.search_bar)

        # -- pages ---------------------------------------------------------
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        view.set_content(self.stack)

        self.stack.add_named(self._build_no_key(), "no-key")
        self.stack.add_named(self._build_no_service(), "no-service")
        self.stack.add_named(self._build_busy(), "busy")
        self.stack.add_named(self._build_unlock(), "unlock")
        self.stack.add_named(self._build_empty(), "empty")
        self.stack.add_named(self._build_list(), "list")
        self.stack.set_visible_child_name("no-key")

        # -- actions & shortcuts ------------------------------------------
        self._add_action("add", lambda *_: self._show_add_dialog(), ["<Control>n"])
        self._add_action("refresh", lambda *_: self.backend.refresh(), ["F5", "<Control>r"])
        self._add_action("forget-password", lambda *_: self.backend.forget_password())
        self._add_action("search", lambda *_: self.search_bar.set_search_mode(True), ["<Control>f"])
        self._add_action("close", lambda *_: self.close(), ["<Control>w"])
        self._add_action(
            "preferences", lambda *_: PreferencesDialog(self._pref_changed).present(self), ["<Control>comma"]
        )
        self._add_action("device-info", lambda *_: self._show_device_info(), ["<Control>i"])
        self._add_action("password", lambda *_: self._show_password_dialog())
        self._add_action("reset-oath", lambda *_: self._confirm_reset())
        self.device_action = Gio.SimpleAction.new_stateful(
            "device", GLib.VariantType.new("s"), GLib.Variant("s", "")
        )
        self.device_action.connect("activate", self._device_chosen)
        self.add_action(self.device_action)
        for name in ("device-info", "password", "reset-oath"):
            self.lookup_action(name).set_enabled(False)

    # -- construction --------------------------------------------------------

    def _menu_model(self):
        m = Gio.Menu()
        key = Gio.Menu()
        key.append("Device info", "win.device-info")
        key.append("Set or change password…", "win.password")
        key.append("Forget saved password", "win.forget-password")
        key.append("Reset OATH…", "win.reset-oath")
        m.append_section(None, key)
        rest = Gio.Menu()
        rest.append("Refresh", "win.refresh")
        rest.append("Preferences", "win.preferences")
        rest.append("About YubiOath", "app.about")
        m.append_section(None, rest)
        return m

    def _add_action(self, name, cb, accels=None):
        a = Gio.SimpleAction.new(name, None)
        a.connect("activate", cb)
        self.add_action(a)
        if accels:
            self.get_application().set_accels_for_action(f"win.{name}", accels)

    def _build_no_key(self) -> Gtk.Widget:
        return Adw.StatusPage(
            icon_name="yubioath-key-symbolic",
            title="Insert your YubiKey",
            description="Waiting for a YubiKey with OATH support…",
        )

    def _build_no_service(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            icon_name="dialog-warning-symbolic",
            title="Smart card service not running",
            description="YubiOath talks to the YubiKey through pcscd. Install the CCID driver and start the service:",
        )
        cmd = Gtk.Label(label=pcsc_install_hint())
        cmd.set_selectable(True)
        cmd.add_css_class("monospace")
        cmd.add_css_class("card")
        cmd.set_margin_start(24)
        cmd.set_margin_end(24)
        cmd.set_xalign(0)
        page.set_child(cmd)
        return page

    def _build_busy(self) -> Gtk.Widget:
        return Adw.StatusPage(
            icon_name="system-lock-screen-symbolic",
            title="YubiKey is in use",
            description=(
                "Another program holds the YubiKey's smart card interface, "
                "usually Yubico Authenticator or GnuPG's scdaemon. "
                "Close it and the key will appear here."
            ),
        )

    def _build_empty(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            icon_name="list-add-symbolic",
            title="No accounts",
            description="Add an account by scanning a QR code or pasting an otpauth:// URI.",
        )
        btn = Gtk.Button(label="Add Account", halign=Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.set_action_name("win.add")
        page.set_child(btn)
        return page

    def _build_unlock(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            icon_name="channel-secure-symbolic",
            title="YubiKey is locked",
            description="Enter the OATH password for this key.",
        )
        clamp = Adw.Clamp(maximum_size=360)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("unlock-box")
        group = Adw.PreferencesGroup()
        self.password_row = Adw.PasswordEntryRow(title="Password")
        self.password_row.connect("entry-activated", lambda *_: self._unlock())
        self.remember_row = Adw.SwitchRow(
            title="Remember on this computer", subtitle="Stored in the system keyring"
        )
        group.add(self.password_row)
        group.add(self.remember_row)
        box.append(group)
        self.unlock_btn = Gtk.Button(label="Unlock", halign=Gtk.Align.CENTER)
        self.unlock_btn.add_css_class("pill")
        self.unlock_btn.add_css_class("suggested-action")
        self.unlock_btn.connect("clicked", lambda *_: self._unlock())
        box.append(self.unlock_btn)
        clamp.set_child(box)
        page.set_child(clamp)
        return page

    def _build_list(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(12)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.connect("row-activated", lambda _lb, row: self._row_primary(row))
        clamp.set_child(self.listbox)
        scroller.set_child(clamp)
        return scroller

    # -- backend callbacks ---------------------------------------------------

    def _on_service(self, ok: bool) -> None:
        self._service_ok = ok
        if not ok:
            self._clear_rows()
            self.stack.set_visible_child_name("no-service")
        elif self._state is None:
            self.stack.set_visible_child_name("no-key")
        self._changed()

    def _on_devices(self, devices, active: str | None) -> None:
        menu = Gio.Menu()
        for d in devices:
            item = Gio.MenuItem.new(d.label, None)
            item.set_action_and_target_value("win.device", GLib.Variant("s", d.fingerprint))
            menu.append_item(item)
        self.device_btn.set_menu_model(menu)
        self.device_btn.set_visible(len(devices) > 1)
        self.device_action.set_state(GLib.Variant("s", active or ""))

    def _device_chosen(self, action, value) -> None:
        action.set_state(value)
        self.backend.select_device(value.get_string())

    def _on_device(self, state: DeviceState | None) -> None:
        log.debug("on_device: %r", state)
        self._state = state
        self._update_device(state)
        self._changed()

    def _update_device(self, state: DeviceState | None) -> None:
        usable = state is not None and not state.busy
        for name in ("device-info", "password", "reset-oath"):
            self.lookup_action(name).set_enabled(usable)
        if state is None:
            self.title_widget.set_subtitle("")
            self._clear_rows()
            if self._service_ok:
                self.stack.set_visible_child_name("no-key")
            return
        if state.busy:
            self.title_widget.set_subtitle("")
            self._clear_rows()
            self.stack.set_visible_child_name("busy")
            return
        sub = state.name + (f" · {state.serial}" if state.serial else "")
        self.title_widget.set_subtitle(sub)
        if state.locked:
            self.unlock_btn.set_sensitive(True)
            self.password_row.set_sensitive(True)
            if state.auth_failure == "typed":
                self.password_row.add_css_class("error")
            else:
                self.password_row.remove_css_class("error")
            self.stack.set_visible_child_name("unlock")
            self.password_row.grab_focus()

    def _on_accounts(self, creds: list[Credential], codes: dict[bytes, Code]) -> None:
        self.password_row.set_text("")
        self.password_row.remove_css_class("error")
        self._creds = creds
        hide = bool(config.get("hide_codes"))
        ordered = self._ordered(creds)
        seen = set()
        for i, cred in enumerate(ordered):
            seen.add(cred.id)
            row = self.rows.get(cred.id)
            if row is None:
                row = AccountRow(cred)
                row.connect("copy-requested", self._row_copy)
                row.connect("calculate-requested", self._row_calculate)
                row.connect("rename-requested", self._row_rename)
                row.connect("delete-requested", self._row_delete)
                row.connect("favorite-toggled", self._row_favorite)
                row.hide_codes = hide
                self._decorate(row)
                self.rows[cred.id] = row
                self.listbox.insert(row, i)
            else:
                row.cred = cred
                row.update_labels()
                self._decorate(row)
                if row.get_index() != i:
                    self.listbox.remove(row)
                    self.listbox.insert(row, i)
            row.set_code(codes.get(cred.id))
        for cid in list(self.rows):
            if cid not in seen:
                self.listbox.remove(self.rows.pop(cid))
        self.stack.set_visible_child_name("list" if creds else "empty")
        self.listbox.invalidate_filter()
        self._schedule_refresh(codes.values())
        self._update_ticking()
        self._changed()

    def _on_code(self, cred: Credential, code: Code) -> None:
        row = self.rows.get(cred.id)
        if row is None:
            return
        row.set_code(code)
        row.reveal()
        self._update_ticking()
        self._copy(row)

    # -- ordering, favorites, icons ------------------------------------------

    def _is_fav(self, cred: Credential) -> bool:
        return config.is_favorite(cred.device_id, cred.id)

    def _ordered(self, creds: list[Credential]) -> list[Credential]:
        return sorted(
            creds, key=lambda c: (not self._is_fav(c), (c.issuer or c.name).lower(), c.name.lower())
        )

    def _decorate(self, row: AccountRow) -> None:
        row.set_favorite(self._is_fav(row.cred))
        if self.icon_pack is not None:
            row.set_avatar_visible(True)
            row.set_icon(self.icon_pack.lookup(row.cred.issuer, row.cred.name))
        else:
            row.set_avatar_visible(False)

    def _reorder(self) -> None:
        for i, cred in enumerate(self._ordered(self._creds)):
            row = self.rows.get(cred.id)
            if row is not None and row.get_index() != i:
                self.listbox.remove(row)
                self.listbox.insert(row, i)
        self._changed()

    def _row_favorite(self, row: AccountRow) -> None:
        config.set_favorite(row.cred.device_id, row.cred.id, not row.favorite)
        row.set_favorite(not row.favorite)
        self._reorder()

    def _pref_changed(self, key: str) -> None:
        if key == "theme":
            self.get_application()._apply_theme(config.get("theme"))
        elif key == "icon_pack":
            self.icon_pack = load_pack(config.get("icon_pack"))
            if config.get("icon_pack") and self.icon_pack is None:
                self._toast("Could not load icon pack")
            for row in self.rows.values():
                self._decorate(row)
            self._png_cache.clear()
            self._changed()
        elif key == "hide_codes":
            for row in self.rows.values():
                row.set_hide_codes(bool(config.get("hide_codes")))
        elif key in ("tray_icon", "close_to_tray"):
            self._apply_tray_prefs()
            if key == "tray_icon":
                self.get_application().set_tray_enabled(bool(config.get("tray_icon")))

    def _apply_tray_prefs(self) -> None:
        self.set_hide_on_close(bool(config.get("tray_icon")) and bool(config.get("close_to_tray")))

    def _on_error(self, msg: str) -> None:
        for row in self.rows.values():
            if row._pending:
                row.set_code(None)
        self._toast(msg)

    # -- code refresh timing -------------------------------------------------

    def _schedule_refresh(self, codes) -> None:
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None
        expiries = [c.valid_to for c in codes if c is not None and c.valid_to < 2**31]
        now = time.time()
        delay = max(0.3, min(expiries) - now + 0.25) if expiries else 30.0
        self._refresh_source = GLib.timeout_add(int(delay * 1000), self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> bool:
        self._refresh_source = None
        # A hidden window (closed to tray) does not poll the key; the tray
        # calculates on click and _visible_changed catches up on show.
        if self.stack.get_visible_child_name() == "list" and self.is_visible():
            self.backend.refresh()
        return False

    def _visible_changed(self, *_) -> None:
        if (
            self.is_visible()
            and self.stack.get_visible_child_name() == "list"
            and self._refresh_source is None
        ):
            self.backend.refresh()
        self._update_ticking()
        self._changed()

    # -- countdown animation -------------------------------------------------
    # The 4 Hz timer only runs while the window is visible and at least one
    # row has a TOTP code to count down; hidden in the tray, nothing wakes up.

    def _update_ticking(self) -> None:
        wanted = self.is_visible() and any(r.needs_tick for r in self.rows.values())
        if wanted and self._tick_source is None:
            self._tick_source = GLib.timeout_add(250, self._tick)
        elif not wanted and self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None

    def _tick(self) -> bool:
        now = time.time()
        for row in self.rows.values():
            row.tick(now)
        if not any(r.needs_tick for r in self.rows.values()):
            self._tick_source = None
            return False
        return True

    # -- row actions ---------------------------------------------------------

    def _row_primary(self, row: AccountRow) -> None:
        if row.code is not None and (row.cred.oath_type == OATH_TYPE.HOTP or row.code.valid_to > time.time()):
            row.reveal()
            self._copy(row)
        else:
            self._row_calculate(row)

    def _row_copy(self, row: AccountRow) -> None:
        if row.code is None:
            self._row_calculate(row)
        else:
            self._copy(row)

    def _copy(self, row: AccountRow) -> None:
        secs = int(config.get("clipboard_clear") or 0)
        # Without focus (tray click) our Wayland input serial may be stale.
        self.clipboard.copy(row.code.value, secs, external=not self.is_active())
        row.flash_copied()
        label = row.cred.issuer or row.cred.name
        self._toast(f"Code for {label} copied", 2)

    def _row_calculate(self, row: AccountRow) -> None:
        if row._pending:
            return
        row.set_pending()
        if row.cred.touch_required:
            self._toast("Touch your YubiKey", 10)
        self.backend.calculate(row.cred)

    def _row_rename(self, row: AccountRow) -> None:
        dlg = Adw.AlertDialog(heading="Rename Account")
        group = Adw.PreferencesGroup()
        issuer = Adw.EntryRow(title="Issuer", text=row.cred.issuer or "")
        name = Adw.EntryRow(title="Account name", text=row.cred.name)
        group.add(issuer)
        group.add(name)
        dlg.set_extra_child(group)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("rename", "Rename")
        dlg.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("rename")

        def done(_d, resp):
            if resp == "rename" and name.get_text().strip():
                self.backend.rename(row.cred, name.get_text().strip(), issuer.get_text().strip() or None)

        dlg.connect("response", done)
        dlg.present(self)

    def _row_delete(self, row: AccountRow) -> None:
        label = f"{row.cred.issuer} ({row.cred.name})" if row.cred.issuer else row.cred.name
        dlg = Adw.AlertDialog(
            heading="Delete Account?",
            body=f"{label} will be removed from the YubiKey. This cannot be undone.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda _d, r: r == "delete" and self.backend.delete(row.cred))
        dlg.present(self)

    # -- misc ----------------------------------------------------------------

    def _unlock(self) -> None:
        pw = self.password_row.get_text()
        if not pw:
            return
        self.unlock_btn.set_sensitive(False)
        self.password_row.set_sensitive(False)
        self.backend.unlock(pw, self.remember_row.get_active())

    def _show_device_info(self) -> None:
        if self._state is not None:
            DeviceInfoDialog(self._state).present(self)

    def _show_password_dialog(self) -> None:
        state = self._state
        if state is None:
            return

        def on_set(pw, remember, done):
            self.backend.set_password(
                pw, remember, lambda err: (done(err), err or self._toast("Password saved", 2))
            )

        def on_remove(done):
            self.backend.remove_password(lambda err: (done(err), err or self._toast("Password removed", 2)))

        PasswordDialog(state.has_password, on_set, on_remove).present(self)

    def _confirm_reset(self) -> None:
        dlg = Adw.AlertDialog(
            heading="Reset OATH?",
            body="All accounts stored on this YubiKey and its OATH password will be erased. "
            "This cannot be undone and the accounts cannot be recovered.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Erase everything")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")

        def done(_d, resp):
            if resp == "reset":
                self.backend.reset_oath(lambda err: self._toast(err or "OATH reset", 3))

        dlg.connect("response", done)
        dlg.present(self)

    def _show_add_dialog(self) -> None:
        if self._state is None:
            self._toast("Insert a YubiKey first")
            return
        dlg = AddAccountDialog(lambda data, touch: self._add(dlg, data, touch))
        dlg.present(self)

    def _add(self, dlg: AddAccountDialog, data: CredentialData, touch: bool) -> None:
        def done(err: str | None):
            if err:
                dlg.failed(err)
            else:
                dlg.close()
                self._toast("Account added", 2)

        self.backend.add(data, touch, done)

    def _filter_row(self, row: AccountRow) -> bool:
        if not self.search_bar.get_search_mode():
            return True
        q = self.search_entry.get_text().strip().lower()
        return all(part in row.search_text for part in q.split())

    def _activate_first_visible(self, *_) -> None:
        i = 0
        while (row := self.listbox.get_row_at_index(i)) is not None:
            if self._filter_row(row):
                self._row_primary(row)
                return
            i += 1

    def _clear_rows(self) -> None:
        for row in self.rows.values():
            self.listbox.remove(row)
        self.rows.clear()
        self._update_ticking()

    def _toast(self, msg: str, timeout: int = 4) -> None:
        if self.is_active():
            self.toasts.add_toast(Adw.Toast(title=msg, timeout=timeout))
        else:  # hidden or unfocused: a toast would go unseen
            self.get_application().notify(msg, timeout)

    def _changed(self) -> None:
        self.emit("accounts-changed")

    # -- tray ----------------------------------------------------------------

    def tray_menu(self) -> list[MenuItem]:
        app = self.get_application()
        items: list[MenuItem] = []
        state = self._state
        if not self._service_ok:
            items.append(MenuItem("Smart card service not running", enabled=False))
        elif state is None:
            items.append(MenuItem("No YubiKey", enabled=False))
        elif state.busy:
            items.append(MenuItem("YubiKey is in use", enabled=False))
        elif state.locked:
            items.append(MenuItem("Unlock YubiKey…", app.show_window))
        elif not self._creds:
            items.append(MenuItem("No accounts", enabled=False))
        else:
            ordered = self._ordered(self._creds)
            n_fav = sum(1 for c in ordered if self._is_fav(c))
            for i, cred in enumerate(ordered):
                if n_fav and i == n_fav:
                    items.append(MenuItem(separator=True))
                label = f"{cred.issuer} · {cred.name}" if cred.issuer else cred.name
                if cred.touch_required:
                    label += "  (touch)"
                items.append(MenuItem(label, lambda c=cred: self.copy_code(c), icon_png=self._icon_png(cred)))
        items.append(MenuItem(separator=True))
        if self.is_visible():
            items.append(MenuItem("Hide YubiOath", app.hide_window))
        else:
            items.append(MenuItem("Show YubiOath", app.show_window))
        items.append(MenuItem("Quit", app.quit))
        return items

    def tray_tooltip(self) -> str:
        state = self._state
        if state is None or state.busy:
            return "No YubiKey"
        return state.name + (f" · {state.serial}" if state.serial else "")

    def copy_code(self, cred: Credential) -> None:
        """Tray entry point: copy a current code, calculating first if needed."""
        row = self.rows.get(cred.id)
        if row is not None:
            self._row_primary(row)

    def _icon_png(self, cred: Credential) -> bytes | None:
        if self.icon_pack is None:
            return None
        texture = self.icon_pack.lookup(cred.issuer, cred.name)
        if texture is None:
            return None
        key = id(texture)
        if key not in self._png_cache:
            try:
                self._png_cache[key] = texture.save_to_png_bytes().get_data()
            except Exception:  # noqa: BLE001
                self._png_cache[key] = b""
        return self._png_cache[key] or None
