import time

import pytest
from smartcard import scard as sc

from yubioath_gtk import pcsc


def test_reader_names_shape():
    names, ok = pcsc.reader_names()
    assert isinstance(names, list)
    assert isinstance(ok, bool)
    if not ok:
        assert names == []


def test_yubikey_busy_ignores_other_readers():
    assert pcsc.yubikey_busy([]) is False
    assert pcsc.yubikey_busy(["Generic CCID Reader 00 00"]) is False


def test_is_card_gone_walks_the_cause_chain():
    class PcscError(Exception):
        def __init__(self, hresult):
            super().__init__("boom")
            self.hresult = hresult

    gone = PcscError(sc.SCARD_W_REMOVED_CARD)
    assert pcsc.is_card_gone(gone)
    assert not pcsc.is_card_gone(PcscError(sc.SCARD_E_SHARING_VIOLATION))
    assert not pcsc.is_card_gone(RuntimeError("x"))
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = gone
    assert pcsc.is_card_gone(wrapped)


def test_significant_drops_in_use_and_changed_bits():
    present = sc.SCARD_STATE_PRESENT
    assert pcsc.significant(present | sc.SCARD_STATE_INUSE | sc.SCARD_STATE_CHANGED) == present
    assert pcsc.significant(present | sc.SCARD_STATE_EXCLUSIVE) == present | sc.SCARD_STATE_EXCLUSIVE
    assert pcsc.significant(sc.SCARD_STATE_EMPTY) != pcsc.significant(present)


def test_watcher_starts_and_stops_promptly():
    events = []
    w = pcsc.ReaderWatcher(lambda: events.append(time.time()))
    w.start()
    time.sleep(0.3)
    assert w.is_alive()
    t0 = time.time()
    w.stop()
    w.join(3)
    assert not w.is_alive()
    assert time.time() - t0 < 2.5
    # Without pcscd the watcher reports "something to check" and backs off; with
    # pcscd and no key it sleeps silently in the kernel.
    _names, ok = pcsc.reader_names()
    if ok:
        assert events == []
    else:
        assert len(events) >= 1


@pytest.mark.skipif(not pcsc.reader_names()[1], reason="pcscd not running")
def test_watcher_idle_uses_no_cpu():
    import resource

    w = pcsc.ReaderWatcher(lambda: None)
    before = resource.getrusage(resource.RUSAGE_SELF)
    w.start()
    time.sleep(1.0)
    after = resource.getrusage(resource.RUSAGE_SELF)
    w.stop()
    w.join(3)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    assert cpu < 0.05  # blocking in the kernel, not spinning
