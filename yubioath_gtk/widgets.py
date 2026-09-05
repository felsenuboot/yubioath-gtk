from __future__ import annotations

import math
import time

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from yubikit.oath import OATH_TYPE, Code, Credential  # noqa: E402


# An expired code stays on screen, dimmed, until the refresh replaces it; if
# nothing does within this many seconds (key gone, window hidden) it is dropped.
EXPIRED_GRACE = 5.0


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
        # Colour comes from CSS (.countdown-ring / .urgent in style.css), so it
        # follows the accent colour and the theme without lookup_color().
        self.add_css_class("countdown-ring")
        self._fraction = 0.0
        self._urgent = False
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction: float, urgent: bool) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if fraction != self._fraction or urgent != self._urgent:
            self._fraction = fraction
            if urgent != self._urgent:
                self._urgent = urgent
                if urgent:
                    self.add_css_class("urgent")
                else:
                    self.remove_css_class("urgent")
            self.queue_draw()

    def _draw(self, area, cr, w, h) -> None:
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 2.5
        color = self.get_color()
        cr.set_line_width(3.0)
        cr.set_source_rgba(color.red, color.green, color.blue, 0.15)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if self._fraction <= 0:
            return
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        start = -math.pi / 2
        cr.arc(cx, cy, r, start, start + 2 * math.pi * self._fraction)
        cr.stroke()


class AccountRow(Gtk.ListBoxRow):
    """One OATH credential: issuer/name on the left, code and ring on the right."""

    __gsignals__ = {
        "copy-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "calculate-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "rename-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "favorite-toggled": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, cred: Credential) -> None:
        super().__init__()
        self.cred = cred
        self.code: Code | None = None
        self._pending = False
        self._expired = False
        self.favorite = False
        self.hide_codes = False
        self.revealed = False
        self.add_css_class("account-row")
        self.set_activatable(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(14)
        box.set_margin_end(10)

        self.avatar = Adw.Avatar(size=36, show_initials=True)
        self.avatar.set_valign(Gtk.Align.CENTER)
        self.avatar.set_visible(False)
        box.append(self.avatar)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.issuer_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.issuer_label.add_css_class("heading")
        self.star = Gtk.Image.new_from_icon_name("starred-symbolic")
        self.star.set_pixel_size(12)
        self.star.add_css_class("favorite-star")
        self.star.set_visible(False)
        title.append(self.issuer_label)
        title.append(self.star)
        self.name_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.name_label.add_css_class("dim-label")
        self.name_label.add_css_class("caption")
        text.append(title)
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
        self.avatar.set_text(c.issuer or c.name)

    def set_favorite(self, fav: bool) -> None:
        self.favorite = fav
        self.star.set_visible(fav)
        self.fav_button.set_label("Remove from favorites" if fav else "Add to favorites")

    def set_icon(self, paintable) -> None:
        """Show an issuer logo (any Gdk.Paintable), or initials when None."""
        self.avatar.set_custom_image(paintable)

    def set_avatar_visible(self, visible: bool) -> None:
        self.avatar.set_visible(visible)

    def set_hide_codes(self, hide: bool) -> None:
        self.hide_codes = hide
        self.revealed = False
        self._refresh_indicator()

    def reveal(self) -> None:
        self.revealed = True
        self._refresh_indicator()

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
        self._expired = False
        self._refresh_indicator()

    def set_pending(self) -> None:
        self._pending = True
        self.indicator.set_visible_child_name("busy")

    @property
    def needs_tick(self) -> bool:
        """True while there is a TOTP countdown (or an expired code) to animate."""
        return self.code is not None and not self._pending and self.cred.oath_type != OATH_TYPE.HOTP

    def tick(self, now: float) -> None:
        if not self.needs_tick:
            return
        remaining = self.code.valid_to - now
        if remaining <= 0:
            if remaining < -EXPIRED_GRACE:
                self.code = None
                self._expired = False
                self.revealed = False
                self._refresh_indicator()
            elif not self._expired:
                # Keep showing the old code, dimmed, instead of blinking to dots
                # for the half second until the refresh lands.
                self._expired = True
                self.code_label.add_css_class("dim-label")
                self.ring.set_fraction(0, False)
            return
        total = max(self.code.valid_to - self.code.valid_from, 1)
        self.ring.set_fraction(remaining / total, remaining < 5)

    @property
    def code_visible(self) -> bool:
        return self.code is not None and (not self.hide_codes or self.revealed)

    def _refresh_indicator(self) -> None:
        if self.code is None or not self.code_visible:
            self.code_label.set_text("••• •••")
            self.code_label.add_css_class("dim-label")
            if self.code is not None:  # hidden but valid: keep the ring ticking
                self.indicator.set_visible_child_name(
                    "hotp" if self.cred.oath_type == OATH_TYPE.HOTP else "ring"
                )
                self.tick(time.time())
                return
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
            ("Add to favorites", "favorite-toggled", None),
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
            if signal == "favorite-toggled":
                self.fav_button = b
        pop.set_child(box)
        return pop
