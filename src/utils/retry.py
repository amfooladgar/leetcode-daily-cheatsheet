"""A tiny stdlib-only capped-exponential-backoff retry decorator.

The pipeline only needs "retry N times with growing backoff on a specific
exception type", so a small in-house helper avoids pulling in a dependency
(e.g. tenacity) for something ~20 lines can do, keeping the dependency
surface (and CI install time) minimal.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)


def retry(
    *,
    exceptions: tuple[type[BaseException], ...],
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 10.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retries the wrapped function on the given exception types, with
    delay doubling each attempt (capped at max_delay_seconds). Re-raises the
    last exception once attempts are exhausted."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            delay = base_delay_seconds
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203 - clarity over micro-perf here
                    last_exc = exc
                    if attempt == attempts:
                        break
                    log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__qualname__,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, max_delay_seconds)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
