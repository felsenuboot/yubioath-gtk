from __future__ import annotations

import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from yubikit.oath import OATH_TYPE, Code, Credential  # noqa: E402


def format_code(value: str) -> str:
    half = len(value) // 2
    return f"{value[:half]} {value[half:]}"


class CountdownRing(Gtk.DrawingArea):
    """Small ring that empties as the current TOTP window runs out."""

    SIZE = 26

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_valign(Gtk.Align.CENTER)
        self._fraction = 0.0
        self._urgent = False
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction: float, urgent: bool) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if fraction != self._fraction or urgent != self._urgent:
            self._fraction = fraction
            self._urgent = urgent
            self.queue_draw()

    def _draw(self, area, cr, w, h) -> None:
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 2.5
        lw = 3.0
        track = self.get_color()
        cr.set_line_width(lw)
        cr.set_source_rgba(track.red, track.green, track.blue, 0.15)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if self._fraction <= 0:
            return
        if self._urgent:
            fg = _named_color(self, "error_color", (0.88, 0.11, 0.14))
        else:
            fg = _accent(self)
        cr.set_source_rgb(*fg)
        cr.set_line_cap(1)  # ROUND
        start = -math.pi / 2
        cr.arc(cx, cy, r, start, start + 2 * math.pi * self._fraction)
        cr.stroke()


def _accent(widget: Gtk.Widget) -> tuple[float, float, float]:
    try:
        rgba = Adw.StyleManager.get_default().get_accent_color_rgba()
        return (rgba.red, rgba.green, rgba.blue)
    except Exception:  # noqa: BLE001
        return _named_color(widget, "accent_color", (0.21, 0.52, 0.89))


def _named_color(widget: Gtk.Widget, name: str, fallback):
    ok, rgba = widget.get_style_context().lookup_color(name)
    return (rgba.red, rgba.green, rgba.blue) if ok else fallback


class AccountRow(Gtk.ListBoxRow):
    """One OATH credential: issuer/name on the left, code and ring on the right."""

    __gsignals__ = {
        "copy-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "calculate-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "rename-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, cred: Credential) -> None:
        super().__init__()
        self.cred = cred
        self.code: Code | None = None
        self._pending = False
        self.add_css_class("account-row")
        self.set_activatable(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(14)
        box.set_margin_end(10)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        self.issuer_label = Gtk.Label(xalign=0, ellipsize=3)
        self.issuer_label.add_css_class("heading")
        self.name_label = Gtk.Label(xalign=0, ellipsize=3)
        self.name_label.add_css_class("dim-label")
        self.name_label.add_css_class("caption")
        text.append(self.issuer_label)
        text.append(self.name_label)
        box.append(text)

        self.code_label = Gtk.Label(xalign=1)
        self.code_label.add_css_class("otp-code")
        self.code_label.add_css_class("numeric")
        self.code_label.set_valign(Gtk.Align.CENTER)
        box.append(self.code_label)

        self.indicator = Gtk.Stack()
        self.indicator.set_size_request(CountdownRing.SIZE, CountdownRing.SIZE)
        self.indicator.set_valign(Gtk.Align.CENTER)
        self.ring = CountdownRing()
        self.indicator.add_named(self.ring, "ring")
        touch = Gtk.Image.new_from_icon_name("fingerprint-symbolic")
        touch.add_css_class("dim-label")
        self.indicator.add_named(touch, "touch")
        hotp = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        hotp.add_css_class("dim-label")
        self.indicator.add_named(hotp, "hotp")
        spinner = Adw.Spinner()
        self.indicator.add_named(spinner, "busy")
        self.indicator.add_named(Gtk.Box(), "none")
        box.append(self.indicator)

        menu = Gtk.MenuButton(icon_name="view-more-symbolic")
        menu.add_css_class("flat")
        menu.add_css_class("circular")
        menu.set_valign(Gtk.Align.CENTER)
        menu.set_popover(self._build_popover())
        box.append(menu)

        self.set_child(box)
        self.update_labels()
        self._refresh_indicator()

        right = Gtk.GestureClick(button=3)
        right.connect("pressed", lambda g, n, x, y: menu.popup())
        self.add_controller(right)

    # -- content ---------------------------------------------------------

    def update_labels(self) -> None:
        c = self.cred
        if c.issuer:
            self.issuer_label.set_text(c.issuer)
            self.name_label.set_text(c.name)
            self.name_label.set_visible(True)
        else:
            self.issuer_label.set_text(c.name)
            self.name_label.set_visible(False)

    @property
    def search_text(self) -> str:
        return f"{self.cred.issuer or ''} {self.cred.name}".lower()

    def set_code(self, code: Code | None) -> None:
        """Replace the code unless the new one is None and the old one is still valid."""
        self._pending = False
        if code is None and self.code is not None and self.code.valid_to > time.time():
            self._refresh_indicator()
            return
        self.code = code
        self._refresh_indicator()

    def set_pending(self) -> None:
        self._pending = True
        self.indicator.set_visible_child_name("busy")

    def tick(self, now: float) -> None:
        if self.code is None or self._pending:
            return
        if self.cred.oath_type == OATH_TYPE.HOTP:
            return
        remaining = self.code.valid_to - now
        if remaining <= 0:
            self.code = None
            self._refresh_indicator()
            return
        total = max(self.code.valid_to - self.code.valid_from, 1)
        self.ring.set_fraction(remaining / total, remaining < 5)

    def _refresh_indicator(self) -> None:
        if self.code is None:
            self.code_label.set_text("••• •••" if self.cred.oath_type == OATH_TYPE.TOTP else "••• •••")
            self.code_label.add_css_class("dim-label")
            if self.cred.oath_type == OATH_TYPE.HOTP:
                self.indicator.set_visible_child_name("hotp")
            elif self.cred.touch_required:
                self.indicator.set_visible_child_name("touch")
            else:
                self.indicator.set_visible_child_name("ring")
                self.ring.set_fraction(0, False)
            return
        self.code_label.set_text(format_code(self.code.value))
        self.code_label.remove_css_class("dim-label")
        if self.cred.oath_type == OATH_TYPE.HOTP:
            self.indicator.set_visible_child_name("hotp")
        else:
            self.indicator.set_visible_child_name("ring")
            self.tick(time.time())

    def flash_copied(self) -> None:
        self.add_css_class("copied")
        GLib.timeout_add(350, lambda: (self.remove_css_class("copied"), False)[1])

    # -- menu ------------------------------------------------------------

    def _build_popover(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.set_has_arrow(False)
        pop.set_position(Gtk.PositionType.BOTTOM)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("menu")
        for label, signal, cls in (
            ("Copy code", "copy-requested", None),
            ("Rename…", "rename-requested", None),
            ("Delete…", "delete-requested", "destructive-action"),
        ):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.set_halign(Gtk.Align.FILL)
            b.get_child().set_xalign(0)
            if cls:
                b.add_css_class(cls)
            b.connect("clicked", lambda _b, s=signal: (pop.popdown(), self.emit(s)))
            box.append(b)
        pop.set_child(box)
        return pop
