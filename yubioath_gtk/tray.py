"""System tray icon.

GTK4 has no tray support, so this speaks the two D-Bus protocols that tray
hosts understand directly: StatusNotifierItem for the icon itself and
com.canonical.dbusmenu for the menu. Works with waybar, Quickshell, KDE and
most other bars; GNOME needs an AppIndicator extension.

The item lives on its own bus connection so that closing that connection
removes the icon from every host immediately.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from collections.abc import Callable

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from . import APP_ID, APP_NAME  # noqa: E402

log = logging.getLogger(__name__)

ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
APP_ICON = os.path.join(ICONS_DIR, "hicolor", "scalable", "apps", APP_ID + ".svg")

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"

ITEM_XML = f"""<node><interface name="{ITEM_IFACE}">
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <method name="ProvideXdgActivationToken"><arg name="token" type="s" direction="in"/></method>
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="i" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionMovieName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewAttentionIcon"/>
  <signal name="NewOverlayIcon"/><signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

MENU_XML = f"""<node><interface name="{MENU_IFACE}">
  <method name="GetLayout">
    <arg name="parentId" type="i" direction="in"/><arg name="recursionDepth" type="i" direction="in"/>
    <arg name="propertyNames" type="as" direction="in"/>
    <arg name="revision" type="u" direction="out"/><arg name="layout" type="(ia{{sv}}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg name="ids" type="ai" direction="in"/><arg name="propertyNames" type="as" direction="in"/>
    <arg name="properties" type="a(ia{{sv}})" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg name="id" type="i" direction="in"/><arg name="name" type="s" direction="in"/>
    <arg name="value" type="v" direction="out"/>
  </method>
  <method name="Event">
    <arg name="id" type="i" direction="in"/><arg name="eventId" type="s" direction="in"/>
    <arg name="data" type="v" direction="in"/><arg name="timestamp" type="u" direction="in"/>
  </method>
  <method name="EventGroup"><arg name="events" type="a(isvu)" direction="in"/><arg name="idErrors" type="ai" direction="out"/></method>
  <method name="AboutToShow"><arg name="id" type="i" direction="in"/><arg name="needUpdate" type="b" direction="out"/></method>
  <method name="AboutToShowGroup">
    <arg name="ids" type="ai" direction="in"/>
    <arg name="updatesNeeded" type="ai" direction="out"/><arg name="idErrors" type="ai" direction="out"/>
  </method>
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <signal name="ItemsPropertiesUpdated"><arg type="a(ia{{sv}})"/><arg type="a(ias)"/></signal>
  <signal name="LayoutUpdated"><arg name="revision" type="u"/><arg name="parent" type="i"/></signal>
  <signal name="ItemActivationRequested"><arg name="id" type="i"/><arg name="timestamp" type="u"/></signal>
</interface></node>"""


@dataclass
class MenuItem:
    label: str = ""
    on_click: Callable[[], None] | None = None
    enabled: bool = True
    separator: bool = False
    icon_png: bytes | None = None

    def props(self) -> dict[str, GLib.Variant]:
        if self.separator:
            return {"type": GLib.Variant("s", "separator")}
        p = {
            "label": GLib.Variant("s", self.label.replace("_", "__")),  # "_" marks mnemonics
            "enabled": GLib.Variant("b", self.enabled),
        }
        if self.icon_png:
            p["icon-data"] = GLib.Variant("ay", self.icon_png)
        return p


class TrayIcon:
    """`build_menu()` returns the MenuItems to show; `on_activate()` runs on a
    click that the host does not turn into a menu (middle click, usually)."""

    def __init__(
        self,
        build_menu: Callable[[], list[MenuItem]],
        on_activate: Callable[[], None],
        on_token: Callable[[str], None] = lambda t: None,
    ) -> None:
        self._build_menu = build_menu
        self._on_activate = on_activate
        self._on_token = on_token
        self._conn: Gio.DBusConnection | None = None
        self._reg_ids: list[int] = []
        self._watch_id = 0
        self._revision = 0
        self._items: dict[int, MenuItem] = {}
        self._tooltip = ""
        self._pixmaps = _render_pixmaps(APP_ICON)
        self.registered = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            log.warning("tray: no session bus: %s", e)
            return
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        Gio.DBusConnection.new_for_address(address, flags, None, None, self._connected)

    def _connected(self, _src, res) -> None:
        try:
            conn = Gio.DBusConnection.new_for_address_finish(res)
        except GLib.Error as e:
            log.warning("tray: could not connect to session bus: %s", e)
            return
        if self._conn is not None or self._stopped:  # stop() raced the connect
            conn.close_sync(None)
            return
        conn.set_exit_on_close(False)
        self._conn = conn
        item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._reg_ids = [
            conn.register_object(ITEM_PATH, item, self._item_call, self._item_get, None),
            conn.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None),
        ]
        self._rebuild()
        self._watch_id = Gio.bus_watch_name_on_connection(
            conn, WATCHER, Gio.BusNameWatcherFlags.NONE, self._watcher_appeared, self._watcher_vanished
        )

    _stopped = False

    def stop(self) -> None:
        self._stopped = True
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._conn is not None:
            for rid in self._reg_ids:
                self._conn.unregister_object(rid)
            self._reg_ids = []
            with contextlib.suppress(GLib.Error):
                self._conn.close_sync(None)
            self._conn = None
        self.registered = False

    def _watcher_appeared(self, conn, _name, _owner) -> None:
        conn.call(
            WATCHER,
            WATCHER_PATH,
            WATCHER,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (conn.get_unique_name(),)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._registered,
        )

    def _registered(self, conn, res) -> None:
        try:
            conn.call_finish(res)
            self.registered = True
            log.debug("tray: registered with %s", WATCHER)
        except GLib.Error as e:
            log.warning("tray: registration failed: %s", e)

    def _watcher_vanished(self, _conn, _name) -> None:
        self.registered = False

    # -- updates -------------------------------------------------------------

    def refresh(self, tooltip: str = "") -> None:
        """Rebuild the menu from `build_menu()` and tell hosts about it."""
        if self._conn is None:
            return
        if tooltip != self._tooltip:
            self._tooltip = tooltip
            self._emit(ITEM_PATH, ITEM_IFACE, "NewToolTip", None)
        self._rebuild()
        self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated", GLib.Variant("(ui)", (self._revision, 0)))

    def _rebuild(self) -> None:
        self._items = {i + 1: item for i, item in enumerate(self._build_menu())}
        self._revision += 1

    def _emit(self, path: str, iface: str, signal: str, params) -> None:
        try:
            self._conn.emit_signal(None, path, iface, signal, params)
        except GLib.Error as e:
            log.debug("tray: emit %s failed: %s", signal, e)

    # -- StatusNotifierItem --------------------------------------------------

    def _item_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate"):
            self._on_activate()
        elif method == "ProvideXdgActivationToken":
            self._on_token(params.unpack()[0])
        invocation.return_value(None)

    def _item_get(self, _conn, _sender, _path, _iface, prop) -> GLib.Variant:
        v = GLib.Variant
        return {
            "Category": lambda: v("s", "ApplicationStatus"),
            "Id": lambda: v("s", APP_ID),
            "Title": lambda: v("s", APP_NAME),
            "Status": lambda: v("s", "Active"),
            "WindowId": lambda: v("i", 0),
            "IconName": lambda: v("s", APP_ID),
            "IconPixmap": lambda: v("a(iiay)", self._pixmaps),
            "IconThemePath": lambda: v("s", ICONS_DIR),
            "OverlayIconName": lambda: v("s", ""),
            "OverlayIconPixmap": lambda: v("a(iiay)", []),
            "AttentionIconName": lambda: v("s", ""),
            "AttentionIconPixmap": lambda: v("a(iiay)", []),
            "AttentionMovieName": lambda: v("s", ""),
            "ToolTip": lambda: v("(sa(iiay)ss)", (APP_ID, [], APP_NAME, self._tooltip)),
            "ItemIsMenu": lambda: v("b", True),
            "Menu": lambda: v("o", MENU_PATH),
        }[prop]()

    # -- dbusmenu ------------------------------------------------------------

    def _layout(self, item_id: int) -> tuple:
        """(id, props, children) for one node; children are boxed as variants
        because the type is recursive (`av`)."""
        if item_id == 0:
            children = [GLib.Variant("(ia{sv}av)", self._layout(i)) for i in self._items]
            return (0, {"children-display": GLib.Variant("s", "submenu")}, children)
        return (item_id, self._items[item_id].props(), [])

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        args = params.unpack()
        if method == "GetLayout":
            parent = args[0] if args[0] in self._items else 0
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout(parent))))
        elif method == "GetGroupProperties":
            rows = [(i, self._items[i].props()) for i in args[0] if i in self._items]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
        elif method == "GetProperty":
            item = self._items.get(args[0])
            value = item.props().get(args[1]) if item else None
            if value is None:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", "no such property")
            else:
                invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            self._event(args[0], args[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _data, _ts in args[0]:
                self._event(item_id, event_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _event(self, item_id: int, event_id: str) -> None:
        item = self._items.get(item_id)
        if event_id == "clicked" and item is not None and item.enabled and item.on_click is not None:
            item.on_click()

    def _menu_get(self, _conn, _sender, _path, _iface, prop) -> GLib.Variant:
        v = GLib.Variant
        return {
            "Version": lambda: v("u", 3),
            "TextDirection": lambda: v("s", "ltr"),
            "Status": lambda: v("s", "normal"),
            "IconThemePath": lambda: v("as", [ICONS_DIR]),
        }[prop]()


def _render_pixmaps(path: str, sizes=(22, 32, 48, 64)) -> list[tuple[int, int, bytes]]:
    """Rasterise the app icon as ARGB32 pixmaps for hosts that ignore icon names."""
    out = []
    for size in sizes:
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
        except GLib.Error as e:
            log.debug("tray: cannot render %s: %s", path, e)
            return []
        w, h, rs, nch = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
        px = pb.get_pixels()
        buf = bytearray(w * h * 4)
        for y in range(h):
            row = px[y * rs : y * rs + w * nch]
            for x in range(w):
                r, g, b = row[x * nch : x * nch + 3]
                a = row[x * nch + 3] if nch == 4 else 255
                i = (y * w + x) * 4
                buf[i : i + 4] = (a, r, g, b)
        out.append((w, h, bytes(buf)))
    return out
