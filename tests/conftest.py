"""Keep the test run away from the real user config and keyring."""

import os
import tempfile

# GLib reads XDG_CONFIG_HOME on first use, which happens when yubioath_gtk.config
# is imported below; the global `config` instance must not touch ~/.config.
_tmp = tempfile.mkdtemp(prefix="yubioath-tests-")
os.environ["XDG_CONFIG_HOME"] = _tmp
