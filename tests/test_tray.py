from gi.repository import GLib

from yubioath_gtk.tray import MenuItem, TrayIcon


def test_menuitem_props_escape_mnemonic_underscores():
    p = MenuItem("my_account", enabled=False).props()
    assert p["label"].unpack() == "my__account"
    assert p["enabled"].unpack() is False
    assert "icon-data" not in p


def test_menuitem_icon_and_separator():
    assert MenuItem(separator=True).props() == {"type": GLib.Variant("s", "separator")}
    p = MenuItem("x", icon_png=b"\x89PNG").props()
    assert bytes(p["icon-data"].unpack()) == b"\x89PNG"


def build_tray(items):
    return TrayIcon(lambda: items, lambda: None)


def test_layout_numbers_items_from_one_and_nests_children_as_variants():
    clicked = []
    items = [
        MenuItem("A", lambda: clicked.append("A")),
        MenuItem(separator=True),
        MenuItem("Quit", lambda: clicked.append("Q")),
    ]
    tray = build_tray(items)
    tray._rebuild()
    root_id, root_props, children = tray._layout(0)
    assert root_id == 0
    assert root_props["children-display"].unpack() == "submenu"
    ids = [c.unpack()[0] for c in children]
    assert ids == [1, 2, 3]
    assert children[1].unpack()[1] == {"type": "separator"}
    # a clicked event runs the item's callback, disabled items are ignored
    tray._event(1, "clicked")
    tray._event(2, "clicked")
    tray._event(99, "clicked")
    assert clicked == ["A"]


def test_rebuild_bumps_revision_only_when_the_menu_looks_different():
    items = [MenuItem("A", lambda: None)]
    tray = build_tray(items)
    r0 = tray._revision
    assert tray._rebuild() is True
    items[0] = MenuItem("A", lambda: None)  # same label, new callback
    assert tray._rebuild() is False
    assert tray._revision == r0 + 1
    assert tray._items[1] is items[0]  # but the fresh callback is kept
    items[0] = MenuItem("A", enabled=False)
    assert tray._rebuild() is True
    items.append(MenuItem("B"))
    assert tray._rebuild() is True
    assert tray._revision == r0 + 3


def test_disabled_item_does_not_fire():
    fired = []
    tray = build_tray([MenuItem("A", lambda: fired.append(1), enabled=False)])
    tray._rebuild()
    tray._event(1, "clicked")
    assert fired == []
