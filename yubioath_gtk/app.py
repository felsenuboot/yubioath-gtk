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
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        if os.environ.get("YUBIOATH_FAKE"):
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
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        self.backend.start()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self, self.backend)
        self.window.present()

    def do_shutdown(self) -> None:
        self.backend.stop()
        Adw.Application.do_shutdown(self)

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
