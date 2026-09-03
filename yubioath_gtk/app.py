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
            from ._fake import FakeBackend  # noqa: PLC0415

            self.backend = FakeBackend()
        else:
            self.backend = Backend()
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            os.path.join(HERE, "icons")
        )
        from .config import config  # noqa: PLC0415

        self._apply_theme(config.get("theme"))
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self, self.backend)
            self.backend.start()
            # Dev aid: YUBIOATH_FAKE=1 YUBIOATH_OPEN=<win action> opens a dialog on launch.
            if os.environ.get("YUBIOATH_FAKE") and os.environ.get("YUBIOATH_OPEN"):
                GLib.timeout_add(1200, lambda: (self.window.activate_action("win." + os.environ["YUBIOATH_OPEN"], None), False)[1])
        self.window.present()

    def do_shutdown(self) -> None:
        self.backend.stop()
        Adw.Application.do_shutdown(self)

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
