from yubioath_gtk.sysinfo import PCSC_GENERIC_HINT, parse_os_release, pcsc_install_hint, stack_too_old


def test_parse_os_release_strips_quotes_and_comments():
    rel = parse_os_release("NAME=\"Arch Linux\"\n# c\nID=arch\nID_LIKE='archlinux'\n\nBROKEN\n")
    assert rel == {"NAME": "Arch Linux", "ID": "arch", "ID_LIKE": "archlinux"}


def test_hint_by_id_and_id_like():
    assert "pacman" in pcsc_install_hint({"ID": "arch"})
    assert "pacman" in pcsc_install_hint({"ID": "endeavouros", "ID_LIKE": "arch"})
    assert "apt" in pcsc_install_hint({"ID": "ubuntu", "ID_LIKE": "debian"})
    assert "apt" in pcsc_install_hint({"ID": "zorin", "ID_LIKE": "ubuntu debian"})
    assert "dnf" in pcsc_install_hint({"ID": "fedora"})
    assert "zypper" in pcsc_install_hint({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"})
    assert "configuration.nix" in pcsc_install_hint({"ID": "nixos"})


def test_hint_falls_back_to_generic():
    assert pcsc_install_hint({"ID": "haiku"}) == PCSC_GENERIC_HINT
    assert pcsc_install_hint({}) == PCSC_GENERIC_HINT


def test_stack_check_runs():
    result = stack_too_old()
    assert result is None or result.startswith("YubiOath cannot start")
