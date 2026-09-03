# YubiOath

OATH one-time passwords (TOTP/HOTP) from a YubiKey, in a small GTK4 + libadwaita
window. A Linux-native replacement for the OTP part of Yubico Authenticator.

- Lists the accounts stored on the key, shows TOTP codes with a countdown ring
- Click a row to copy the code; touch-required and HOTP accounts calculate on click
- Password-protected keys: unlock once, optionally remember in the system keyring
- Add accounts from an `otpauth://` URI, a QR code on screen (grim + zbarimg), or by hand
- Rename and delete accounts, search with Ctrl+F
- Follows the system light/dark theme and accent colour

Talks to the key directly through [yubikey-manager](https://github.com/Yubico/yubikey-manager)
(`yubikit`) over PC/SC. No background service of its own.

## Requirements (Arch)

```sh
sudo pacman -S --needed yubikey-manager ccid python-gobject gtk4 libadwaita libsecret
sudo systemctl enable --now pcscd.socket
# optional, for "Scan QR code on screen":
sudo pacman -S --needed grim zbar
```

## Install / run

```sh
./install.sh        # symlinks bin/yubioath-gtk into ~/.local/bin, installs .desktop + icon
yubioath-gtk
```

Or run from the checkout without installing: `python -m yubioath_gtk`.

Environment variables: `YUBIOATH_DEBUG=1` for verbose logging,
`YUBIOATH_FAKE=1` to run with fake accounts and no hardware (UI development).

## Hyprland

The window's app id is `io.github.felsenuboot.YubiOath`. To keep it as a small
floating window, add to your config (Lua config, Hyprland 0.56):

```lua
hl.window_rule({
    name = "yubioath",
    match = { class = "io.github.felsenuboot.YubiOath" },
    float = true,
    center = true,
    size = "420 640",
})
```

Add `pin = true` if it should stay visible on every workspace.

## Keyboard

| Shortcut | Action |
|---|---|
| Ctrl+F | Search; Enter copies the first match |
| Ctrl+N | Add account |
| F5 / Ctrl+R | Refresh |
| Ctrl+Q / Ctrl+W | Quit |

## License

MIT. Not affiliated with Yubico; YubiKey is a trademark of Yubico AB.
