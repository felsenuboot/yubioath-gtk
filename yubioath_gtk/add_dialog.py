from __future__ import annotations

import base64
import shutil
import subprocess
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from yubikit.oath import HASH_ALGORITHM, OATH_TYPE, CredentialData, parse_b32_key  # noqa: E402

ALGOS = [HASH_ALGORITHM.SHA1, HASH_ALGORITHM.SHA256, HASH_ALGORITHM.SHA512]


class AddAccountDialog(Adw.Dialog):
    def __init__(self, on_add: Callable[[CredentialData, bool], None]) -> None:
        super().__init__(title="Add Account", content_width=440)
        self._on_add = on_add

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.add_btn = Gtk.Button(label="Add")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.set_sensitive(False)
        self.add_btn.connect("clicked", self._submit)
        header.pack_end(self.add_btn)
        view.add_top_bar(header)

        self.toasts = Adw.ToastOverlay()
        page = Adw.PreferencesPage()
        self.toasts.set_child(page)
        view.set_content(self.toasts)
        self.set_child(view)

        # -- URI / QR ---------------------------------------------------
        g = Adw.PreferencesGroup(title="From QR code or URI")
        self.uri_row = Adw.EntryRow(title="otpauth:// URI")
        self.uri_row.connect("changed", self._uri_changed)
        g.add(self.uri_row)
        if shutil.which("grim") and shutil.which("zbarimg"):
            scan = Adw.ButtonRow(title="Scan QR code on screen", start_icon_name="camera-photo-symbolic")
            scan.connect("activated", self._scan_screen)
            g.add(scan)
        page.add(g)

        # -- manual -----------------------------------------------------
        g = Adw.PreferencesGroup(title="Account")
        self.issuer_row = Adw.EntryRow(title="Issuer")
        self.name_row = Adw.EntryRow(title="Account name")
        self.secret_row = Adw.PasswordEntryRow(title="Secret key")
        for r in (self.issuer_row, self.name_row, self.secret_row):
            r.connect("changed", lambda *_: self._validate())
            g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup()
        adv = Adw.ExpanderRow(title="Advanced")
        self.type_row = Adw.ComboRow(
            title="Type", model=Gtk.StringList.new(["Time based (TOTP)", "Counter based (HOTP)"])
        )
        self.type_row.connect("notify::selected", lambda *_: self._type_changed())
        self.algo_row = Adw.ComboRow(
            title="Algorithm", model=Gtk.StringList.new(["SHA1", "SHA256", "SHA512"])
        )
        self.digits_row = Adw.ComboRow(title="Digits", model=Gtk.StringList.new(["6", "8"]))
        self.period_row = Adw.SpinRow.new_with_range(15, 300, 1)
        self.period_row.set_title("Period (seconds)")
        self.period_row.set_value(30)
        self.counter_row = Adw.SpinRow.new_with_range(0, 2**31, 1)
        self.counter_row.set_title("Initial counter")
        self.counter_row.set_visible(False)
        self.touch_row = Adw.SwitchRow(title="Require touch")
        for r in (
            self.type_row,
            self.algo_row,
            self.digits_row,
            self.period_row,
            self.counter_row,
            self.touch_row,
        ):
            adv.add_row(r)
        g.add(adv)
        page.add(g)

        self.connect("map", lambda *_: self.uri_row.grab_focus())

    # -- handlers ---------------------------------------------------------

    def _type_changed(self) -> None:
        hotp = self.type_row.get_selected() == 1
        self.period_row.set_visible(not hotp)
        self.counter_row.set_visible(hotp)

    def _uri_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip()
        if not text.startswith("otpauth://"):
            row.remove_css_class("error")
            self._validate()
            return
        try:
            data = CredentialData.parse_uri(text)
        except Exception as e:  # noqa: BLE001
            row.add_css_class("error")
            self.add_btn.set_sensitive(False)
            self.add_btn.set_tooltip_text(str(e))
            return
        row.remove_css_class("error")
        self._fill_from(data)
        self._validate()

    def _fill_from(self, d: CredentialData) -> None:
        self.issuer_row.set_text(d.issuer or "")
        self.name_row.set_text(d.name)
        self.secret_row.set_text(base64.b32encode(d.secret).decode().rstrip("="))
        self.type_row.set_selected(1 if d.oath_type == OATH_TYPE.HOTP else 0)
        self.algo_row.set_selected(ALGOS.index(d.hash_algorithm))
        self.digits_row.set_selected(1 if d.digits == 8 else 0)
        self.period_row.set_value(d.period)
        self.counter_row.set_value(d.counter)

    def _scan_screen(self, *_):
        self.set_sensitive(False)
        # Give the dialog a moment so the QR code is not hidden behind it.
        GLib.timeout_add(150, self._do_scan)

    def _do_scan(self) -> bool:
        try:
            grim, zbarimg = shutil.which("grim"), shutil.which("zbarimg")
            if not grim or not zbarimg:
                raise FileNotFoundError("grim or zbarimg not found")
            shot = subprocess.run([grim, "-"], capture_output=True, check=True, timeout=10).stdout
            out = subprocess.run(
                [zbarimg, "-q", "--raw", "-"], input=shot, capture_output=True, timeout=20, check=False
            ).stdout.decode(errors="replace")
        except Exception as e:  # noqa: BLE001
            self.set_sensitive(True)
            self._toast(f"Scan failed: {e}")
            return False
        self.set_sensitive(True)
        uris = [line.strip() for line in out.splitlines() if line.strip().startswith("otpauth://")]
        if not uris:
            self._toast("No otpauth QR code found on screen")
            return False
        self.uri_row.set_text(uris[0])
        return False

    def _validate(self) -> None:
        ok = bool(self.name_row.get_text().strip()) and bool(self.secret_row.get_text().strip())
        if ok:
            try:
                parse_b32_key(self.secret_row.get_text())
                self.secret_row.remove_css_class("error")
            except Exception:  # noqa: BLE001
                ok = False
                self.secret_row.add_css_class("error")
        self.add_btn.set_sensitive(ok)
        self.add_btn.set_tooltip_text(None)

    def _submit(self, *_):
        try:
            data = CredentialData(
                name=self.name_row.get_text().strip(),
                oath_type=OATH_TYPE.HOTP if self.type_row.get_selected() == 1 else OATH_TYPE.TOTP,
                hash_algorithm=ALGOS[self.algo_row.get_selected()],
                secret=parse_b32_key(self.secret_row.get_text()),
                digits=8 if self.digits_row.get_selected() == 1 else 6,
                period=int(self.period_row.get_value()),
                counter=int(self.counter_row.get_value()),
                issuer=self.issuer_row.get_text().strip() or None,
            )
        except Exception as e:  # noqa: BLE001
            self._toast(str(e))
            return
        self.add_btn.set_sensitive(False)
        self._on_add(data, self.touch_row.get_active())

    def _toast(self, msg: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=4))

    def failed(self, msg: str) -> None:
        self._toast(msg)
        self.add_btn.set_sensitive(True)
