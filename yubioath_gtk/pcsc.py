"""Thin layer over pyscard's low-level API.

Everything here decides by numeric PC/SC result codes, never by parsing error
messages (those differ between pcsc-lite, macOS and Windows), and does no card
I/O beyond a single connect attempt in `yubikey_busy`.

`ReaderWatcher` blocks in SCardGetStatusChange so the backend is woken the
instant a reader appears or disappears or a card changes state, instead of
polling the key every second.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from smartcard import scard as sc

log = logging.getLogger(__name__)

# pcsc-lite (and Windows) report reader arrival/removal through this pseudo reader.
PNP_READER = "\\\\?PnP?\\Notification"

SERVICE_DOWN = {sc.SCARD_E_NO_SERVICE, sc.SCARD_E_SERVICE_STOPPED}
CARD_GONE = {
    sc.SCARD_W_REMOVED_CARD,
    sc.SCARD_E_NO_SMARTCARD,
    sc.SCARD_E_READER_UNAVAILABLE,
    sc.SCARD_E_UNKNOWN_READER,
}
# Bits that flip whenever *anyone* (including us) opens or closes the card.
# Ignoring them keeps our own refreshes from waking the watcher.
NOISE_BITS = sc.SCARD_STATE_INUSE | sc.SCARD_STATE_CHANGED


def reader_names() -> tuple[list[str], bool]:
    """(reader names, service reachable). One SCardListReaders, no card I/O."""
    hr, ctx = sc.SCardEstablishContext(sc.SCARD_SCOPE_USER)
    if hr != sc.SCARD_S_SUCCESS:
        return [], hr not in SERVICE_DOWN
    try:
        hr, readers = sc.SCardListReaders(ctx, [])
        if hr == sc.SCARD_S_SUCCESS:
            return list(readers), True
        if hr == sc.SCARD_E_NO_READERS_AVAILABLE:
            return [], True
        log.debug("SCardListReaders: %s", sc.SCardGetErrorMessage(hr))
        return [], hr not in SERVICE_DOWN
    finally:
        sc.SCardReleaseContext(ctx)


def yubikey_busy(readers: list[str]) -> bool:
    """True when a YubiKey reader exists but another client holds the card
    exclusively (Yubico Authenticator or GnuPG's scdaemon, typically)."""
    yubi = [r for r in readers if "yubi" in r.lower()]
    if not yubi:
        return False
    hr, ctx = sc.SCardEstablishContext(sc.SCARD_SCOPE_USER)
    if hr != sc.SCARD_S_SUCCESS:
        return False
    try:
        for reader in yubi:
            hr, card, _proto = sc.SCardConnect(
                ctx, reader, sc.SCARD_SHARE_SHARED, sc.SCARD_PROTOCOL_T0 | sc.SCARD_PROTOCOL_T1
            )
            if hr == sc.SCARD_E_SHARING_VIOLATION:
                return True
            if hr == sc.SCARD_S_SUCCESS:
                sc.SCardDisconnect(card, sc.SCARD_LEAVE_CARD)
    finally:
        sc.SCardReleaseContext(ctx)
    return False


def is_card_gone(exc: BaseException) -> bool:
    """Does this exception (or its cause) say the card or reader went away?"""
    e: BaseException | None = exc
    while e is not None:
        if getattr(e, "hresult", None) in CARD_GONE:
            return True
        e = e.__cause__ or e.__context__
    return False


def significant(state: int) -> int:
    """The part of a reader state worth waking up for."""
    return state & ~NOISE_BITS


class ReaderWatcher(threading.Thread):
    """Calls `on_change()` from its own thread whenever the set of readers or a
    card's presence/exclusive-use state changes. Sleeps in the kernel in
    between; falls back to a slow poll while pcscd is unreachable."""

    FALLBACK_INTERVAL = 2.0

    def __init__(self, on_change: Callable[[], None]) -> None:
        super().__init__(name="pcsc-watch", daemon=True)
        self._on_change = on_change
        self._stop = threading.Event()
        self._ctx_lock = threading.Lock()
        self._ctx: int | None = None

    def stop(self) -> None:
        self._stop.set()
        with self._ctx_lock:
            if self._ctx is not None:
                sc.SCardCancel(self._ctx)

    def run(self) -> None:
        while not self._stop.is_set():
            hr, ctx = sc.SCardEstablishContext(sc.SCARD_SCOPE_USER)
            if hr != sc.SCARD_S_SUCCESS:
                # pcscd not running: let the backend notice, try again later.
                self._on_change()
                self._stop.wait(self.FALLBACK_INTERVAL)
                continue
            with self._ctx_lock:
                self._ctx = ctx
            try:
                self._watch(ctx)
            finally:
                with self._ctx_lock:
                    self._ctx = None
                sc.SCardReleaseContext(ctx)

    def _watch(self, ctx: int) -> None:
        """Wait for changes on one context until it becomes unusable."""
        known: dict[str, int] | None = None
        while not self._stop.is_set():
            hr, readers = sc.SCardListReaders(ctx, [])
            if hr not in (sc.SCARD_S_SUCCESS, sc.SCARD_E_NO_READERS_AVAILABLE):
                self._fail(hr)
                return
            names = [PNP_READER, *readers]
            # Snapshot the current states (returns at once), then block until
            # any of them differs from the snapshot.
            hr, snapshot = sc.SCardGetStatusChange(ctx, 0, [(n, sc.SCARD_STATE_UNAWARE) for n in names])
            if hr not in (sc.SCARD_S_SUCCESS, sc.SCARD_E_TIMEOUT):
                self._fail(hr)
                return
            current = {name: significant(state) for name, state, _atr in snapshot if name != PNP_READER}
            if known is not None and (current != known):
                self._on_change()  # changed between the wake-up and this snapshot
            known = current
            hr, _ = sc.SCardGetStatusChange(ctx, sc.INFINITE, [(n, s) for n, s, _atr in snapshot])
            if hr == sc.SCARD_E_CANCELLED or self._stop.is_set():
                return
            if hr not in (sc.SCARD_S_SUCCESS, sc.SCARD_E_TIMEOUT):
                self._fail(hr)
                return
            hr, after = sc.SCardGetStatusChange(ctx, 0, [(n, sc.SCARD_STATE_UNAWARE) for n in names])
            now = {name: significant(state) for name, state, _atr in after if name != PNP_READER}
            hr, readers_now = sc.SCardListReaders(ctx, [])
            if now != known or sorted(readers_now or []) != sorted(readers):
                known = now
                self._on_change()

    def _fail(self, hr: int) -> None:
        log.debug("pcsc watch: %s", sc.SCardGetErrorMessage(hr))
        self._on_change()
        self._stop.wait(self.FALLBACK_INTERVAL)
