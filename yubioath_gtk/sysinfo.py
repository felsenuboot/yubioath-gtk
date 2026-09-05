"""Small platform facts: which distro this is (for install hints) and whether
the GTK stack is new enough for the widgets we use."""

from __future__ import annotations

from pathlib import Path

# (os-release IDs, commands). ID_LIKE is consulted too, so derivatives inherit.
PCSC_HINTS: list[tuple[set[str], str]] = [
    (
        {"arch", "manjaro", "endeavouros", "cachyos"},
        "sudo pacman -S --needed ccid\nsudo systemctl enable --now pcscd.socket",
    ),
    (
        {"debian", "ubuntu", "linuxmint", "pop", "elementary"},
        "sudo apt install pcscd\nsudo systemctl enable --now pcscd.socket",
    ),
    (
        {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"},
        "sudo dnf install pcsc-lite ccid\nsudo systemctl enable --now pcscd.socket",
    ),
    (
        {"opensuse", "opensuse-tumbleweed", "opensuse-leap", "suse", "sles"},
        "sudo zypper install pcsc-lite pcsc-ccid\nsudo systemctl enable --now pcscd.socket",
    ),
    ({"nixos"}, "services.pcscd.enable = true;   # configuration.nix, then nixos-rebuild switch"),
    ({"alpine"}, "sudo apk add pcsc-lite ccid\nsudo rc-update add pcscd && sudo rc-service pcscd start"),
    ({"void"}, "sudo xbps-install pcsc-ccid\nsudo ln -s /etc/sv/pcscd /var/service/"),
    ({"gentoo"}, "sudo emerge sys-apps/pcsc-lite app-crypt/ccid\nsudo systemctl enable --now pcscd.socket"),
]
PCSC_GENERIC_HINT = (
    "Install pcsc-lite (pcscd) and the CCID driver with your package manager,\nthen start the pcscd service."
)

# Adw.Spinner and Adw.ButtonRow arrived in libadwaita 1.6, which needs GTK 4.15.
MIN_ADW = (1, 6)
MIN_GTK = (4, 14)


def parse_os_release(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def os_release(path: str = "/etc/os-release") -> dict[str, str]:
    try:
        return parse_os_release(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}


def pcsc_install_hint(release: dict[str, str] | None = None) -> str:
    """Shell commands to get pcscd running on this distro, or generic advice."""
    rel = os_release() if release is None else release
    ids = [rel.get("ID", "").lower(), *rel.get("ID_LIKE", "").lower().split()]
    for candidates, hint in PCSC_HINTS:
        if any(i in candidates for i in ids if i):
            return hint
    return PCSC_GENERIC_HINT


def stack_too_old() -> str | None:
    """A readable reason if GTK or libadwaita are older than what the UI uses."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    gtk = (Gtk.get_major_version(), Gtk.get_minor_version())
    adw = (Adw.get_major_version(), Adw.get_minor_version())
    problems = []
    if gtk < MIN_GTK:
        problems.append(f"GTK {gtk[0]}.{gtk[1]} (need {MIN_GTK[0]}.{MIN_GTK[1]} or newer)")
    if adw < MIN_ADW:
        problems.append(f"libadwaita {adw[0]}.{adw[1]} (need {MIN_ADW[0]}.{MIN_ADW[1]} or newer)")
    if not problems:
        return None
    return "YubiOath cannot start: " + ", ".join(problems) + "."
