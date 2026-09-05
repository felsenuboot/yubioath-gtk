"""YubiOath: OATH one-time passwords from a YubiKey, in GTK4 + libadwaita."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

APP_ID = "io.github.felsenuboot.YubiOath"
APP_NAME = "YubiOath"


def _version() -> str:
    """The version lives in pyproject.toml only. Installed: read the package
    metadata; running from a checkout: read the file itself."""
    try:
        return version("yubioath-gtk")
    except PackageNotFoundError:
        pass
    try:
        with (Path(__file__).resolve().parent.parent / "pyproject.toml").open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


VERSION = _version()
