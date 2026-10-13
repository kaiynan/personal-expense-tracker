"""Rate limiter berbasis sliding window untuk membatasi frekuensi permintaan pengguna."""

from __future__ import annotations

import collections
import threading
import time

import config


class UserRateLimiter:
    """Sliding-window rate limiter per user/key."""

    def __init__(self, default_limit: int | None = None, window_seconds: float = 60.0) -> None:
        self.default_limit = (
            default_limit if default_limit is not None else config.RATE_LIMIT_REQUESTS_PER_MINUTE
        )
        self.window_seconds = window_seconds
        self._records: dict[int | str, collections.deque[float]] = collections.defaultdict(
            collections.deque
        )
        self._lock = threading.Lock()

    def is_allowed(
        self,
        key: int | str,
        limit: int | None = None,
        window_seconds: float | None = None,
    ) -> bool:
        """Periksa apakah permintaan untuk `key` diizinkan dalam sliding window.

        Jika masih dalam batas, waktu permintaan dicatat dan mengembalikan True.
        Jika batas terlampaui, mengembalikan False.
        """
        max_requests = limit if limit is not None else self.default_limit
        if max_requests <= 0:
            return True

        window = window_seconds if window_seconds is not None else self.window_seconds
        now = time.monotonic()

        with self._lock:
            q = self._records[key]
            cutoff = now - window
            while q and q[0] <= cutoff:
                q.popleft()

            if len(q) < max_requests:
                q.append(now)
                return True
            return False

    def remaining(
        self,
        key: int | str,
        limit: int | None = None,
        window_seconds: float | None = None,
    ) -> int:
        """Hitung sisa kuota permintaan untuk `key` dalam window saat ini."""
        max_requests = limit if limit is not None else self.default_limit
        if max_requests <= 0:
            return 999999

        window = window_seconds if window_seconds is not None else self.window_seconds
        now = time.monotonic()

        with self._lock:
            q = self._records[key]
            cutoff = now - window
            while q and q[0] <= cutoff:
                q.popleft()
            return max(0, max_requests - len(q))

    def reset(self, key: int | str | None = None) -> None:
        """Reset riwayat kuota untuk satu key atau seluruhnya."""
        with self._lock:
            if key is None:
                self._records.clear()
            else:
                self._records.pop(key, None)


# Singleton rate limiter aplikasi
global_rate_limiter = UserRateLimiter()
