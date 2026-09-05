from __future__ import annotations

import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION  # noqa: E402
from .backend import Backend  # noqa: E402
from .config import config  # noqa: E402
from .tray import TrayIcon  # noqa: E402
from .window import MainWindow  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class YubiOathApp(Adw.Application):
    def __init__(self) -> None:
        fake = bool(os.environ.get("YUBIOATH_FAKE"))
        super().__init__(
            application_id=APP_ID + (".Devel" if fake else ""), flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        GLib.set_application_name(APP_NAME)
        if fake:
            from ._fake import FakeBackend

            self.backend = FakeBackend()
        else:
            self.backend = Backend()
        self.window: MainWindow | None = None
        self.tray: TrayIcon | None = None
        self._activation_token: str | None = None
        self._notify_source: int | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(os.path.join(HERE, "icons"))
        self._apply_theme(config.get("theme"))
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self) -> None:
        if self.window is not None:  # second launch while running: just raise
            self.show_window()
            return
        self.window = MainWindow(self, self.backend)
        self.window.connect("accounts-changed", self._tray_refresh)
        self.backend.start()
        tray = bool(config.get("tray_icon"))
        self.set_tray_enabled(tray)
        # Dev aid: YUBIOATH_FAKE=1 YUBIOATH_OPEN=<win action> opens a dialog on launch.
        if os.environ.get("YUBIOATH_FAKE") and os.environ.get("YUBIOATH_OPEN"):
            GLib.timeout_add(
                1200,
                lambda: (self.window.activate_action("win." + os.environ["YUBIOATH_OPEN"], None), False)[1],
            )
        if not (tray and config.get("start_hidden")):
            self.window.present()

    def do_shutdown(self) -> None:
        self.set_tray_enabled(False)
        self.backend.stop()
        Adw.Application.do_shutdown(self)

    # -- tray ----------------------------------------------------------------

    def set_tray_enabled(self, on: bool) -> None:
        if on and self.tray is None:
            self.tray = TrayIcon(self.window.tray_menu, self.toggle_window, self._got_activation_token)
            self.tray.start()
        elif not on and self.tray is not None:
            self.tray.stop()
            self.tray = None

    def _tray_refresh(self, *_) -> None:
        if self.tray is not None:
            self.tray.refresh(self.window.tray_tooltip())

    def _got_activation_token(self, token: str) -> None:
        self._activation_token = token

    def show_window(self) -> None:
        if self._activation_token:  # lets the compositor focus us without user input on our surface
            self.window.set_startup_id(self._activation_token)
            self._activation_token = None
        self.window.present()

    def hide_window(self) -> None:
        self.window.set_visible(False)

    def toggle_window(self) -> None:
        if self.window.is_active():
            self.hide_window()
        else:
            self.show_window()

    def notify(self, msg: str, timeout: int = 4) -> None:
        """Desktop notification for feedback the window cannot show right now."""
        n = Gio.Notification.new(msg)
        n.set_icon(Gio.ThemedIcon.new(APP_ID))
        self.send_notification("status", n)
        if self._notify_source:
            GLib.source_remove(self._notify_source)
        self._notify_source = GLib.timeout_add_seconds(timeout, self._withdraw_notification)

    def _withdraw_notification(self) -> bool:
        self._notify_source = None
        self.withdraw_notification("status")
        return False

    _palette_provider: Gtk.CssProvider | None = None

    def _apply_theme(self, theme: str) -> None:
        scheme = {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(theme, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        # A forced theme also re-declares the stock palette above USER priority,
        # so wallpaper colour tools writing ~/.config/gtk-4.0/gtk.css cannot
        # repaint it. "system" keeps whatever the user has configured.
        display = Gdk.Display.get_default()
        if self._palette_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self._palette_provider)
            self._palette_provider = None
        if theme in ("light", "dark"):
            self._palette_provider = Gtk.CssProvider()
            self._palette_provider.load_from_path(os.path.join(HERE, f"palette-{theme}.css"))
            Gtk.StyleContext.add_provider_for_display(
                display, self._palette_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
            )

    def _about(self, *_) -> None:
        dlg = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="felsenuboot",
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/felsenuboot/yubioath-gtk",
            comments="OATH one-time passwords from your YubiKey.",
        )
        dlg.present(self.window)


def main(argv: list[str]) -> int:
    debug = bool(os.environ.get("YUBIOATH_DEBUG"))
    logging.basicConfig(level=logging.DEBUG if debug else logging.WARNING)
    if debug:
        logging.getLogger("ykman").setLevel(logging.DEBUG)
    return YubiOathApp().run(argv)


def main_entry() -> None:
    sys.exit(main(sys.argv))
