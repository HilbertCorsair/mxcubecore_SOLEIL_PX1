"""Waiting on a real condition instead of sleeping a fixed time.

A fixed sleep is either too short (the next step starts on a device that is
still moving) or too long (every sample pays for the worst case). Polling the
state the next step actually depends on costs one short period at most.
"""

import logging
import time

import gevent

log = logging.getLogger("HWR")


def wait_until(predicate, timeout, period=0.1, what="condition"):
    """Poll ``predicate()`` until it is true, for at most ``timeout`` seconds.

    Returns True once it holds, False on timeout. Never raises: an exception
    from the predicate counts as "not yet", and the caller decides what a
    False means.
    """
    t0 = time.time()
    while True:
        try:
            if predicate():
                return True
        except Exception:
            log.debug("wait_until(%s): predicate raised, retrying", what, exc_info=True)
        if time.time() - t0 >= timeout:
            log.warning("Timeout (%.0f s) waiting for %s", timeout, what)
            return False
        gevent.sleep(period)
