"""Fixed-capacity, RAM-only PCM ring buffer; independent of Sonos and cdparanoia.

Offsets are absolute byte positions in one PCM stream. Old PCM survives reader
consumption until the next write actually needs its space. Full-buffer writes
may only evict bytes already passed by every active reader. A pinned oldest
reader causes backpressure instead of data loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
import tomllib

PCM_FRAME_BYTES = 4  # 16-bit stereo
MIB = 1024 * 1024


class BufferErrorBase(Exception):
    pass


class DataExpired(BufferErrorBase):
    """The requested PCM offset is no longer in the bounded buffer."""


class BufferClosed(BufferErrorBase):
    pass


class BufferTimeout(BufferErrorBase):
    pass


@dataclass(frozen=True)
class StreamingConfig:
    max_buffer_mib: int

    @classmethod
    def load(cls, path: str | Path) -> "StreamingConfig":
        with open(path, "rb") as fh:
            config = tomllib.load(fh)
        section = config.get("streaming")
        if not isinstance(section, dict) or "max_buffer_mib" not in section:
            raise ValueError("Missing [streaming].max_buffer_mib")
        value = section["max_buffer_mib"]
        if type(value) is not int or not 1 <= value <= 512:
            raise ValueError("max_buffer_mib must be an integer from 1 to 512")
        return cls(max_buffer_mib=value)

    @property
    def capacity_bytes(self) -> int:
        return self.max_buffer_mib * MIB


class PCMRing:
    def __init__(self, capacity_bytes: int):
        if type(capacity_bytes) is not int or capacity_bytes < PCM_FRAME_BYTES or capacity_bytes % PCM_FRAME_BYTES:
            raise ValueError("capacity_bytes must be a positive multiple of four")
        # The only persistent allocation holding PCM: exactly capacity_bytes.
        self._buf = bytearray(capacity_bytes)
        self.capacity = capacity_bytes
        self._start = 0  # oldest retained absolute PCM offset
        self._end = 0  # one past newest PCM offset
        self._readers: dict[int, int] = {}
        self._next_reader_id = 1
        self._finished = False
        self._error: str | None = None
        self._cv = threading.Condition()
        # Diagnostics only; no additional PCM allocations.
        self._producer_wait_count = 0
        self._producer_wait_seconds = 0.0
        self._consumer_wait_count = 0
        self._consumer_wait_seconds = 0.0
        self._consumer_wait_max_seconds = 0.0

    def reset_for_replay(self, offset: int = 0) -> None:
        """Reuse the exact existing PCM allocation for a new track generation.

        Caller must hold the generation admission lock and guarantee that the
        old CD reader is terminated and all HTTP cursors have unregistered.
        """
        with self._cv:
            if not self._finished or self._readers:
                raise BufferClosed('cannot reset ring while producer or readers active')
            if type(offset) is not int or offset < 0 or offset % PCM_FRAME_BYTES:
                raise ValueError('replay offset must be PCM-frame aligned')
            self._start = self._end = offset
            self._finished = False
            self._error = None
            self._producer_wait_count = 0
            self._producer_wait_seconds = 0.0
            self._consumer_wait_count = 0
            self._consumer_wait_seconds = 0.0
            self._consumer_wait_max_seconds = 0.0
            self._cv.notify_all()

    @property
    def stats(self) -> dict:
        with self._cv:
            return {"capacity_bytes": self.capacity, "oldest_offset": self._start,
                    "next_offset": self._end, "retained_bytes": self._end-self._start,
                    "readers": len(self._readers), "finished": self._finished,
                    "error": self._error}

    @property
    def diagnostics(self) -> dict:
        with self._cv:
            return {
                "capacity_bytes": self.capacity,
                "oldest_offset": self._start,
                "next_offset": self._end,
                "retained_bytes": self._end - self._start,
                "reader_offsets": dict(self._readers),
                "producer_wait_count": self._producer_wait_count,
                "producer_wait_ms": round(self._producer_wait_seconds * 1000, 3),
                "consumer_wait_count": self._consumer_wait_count,
                "consumer_wait_ms": round(self._consumer_wait_seconds * 1000, 3),
                "consumer_wait_max_ms": round(self._consumer_wait_max_seconds * 1000, 3),
                "finished": self._finished, "error": self._error,
            }

    def register(self, offset: int = 0) -> int:
        if type(offset) is not int or offset < 0 or offset % PCM_FRAME_BYTES:
            raise ValueError("reader offset must be a nonnegative PCM-frame-aligned byte offset")
        with self._cv:
            if offset < self._start:
                raise DataExpired(f"offset {offset} has expired; oldest is {self._start}")
            if self._finished and offset > self._end:
                raise ValueError("reader offset beyond finished stream")
            reader_id = self._next_reader_id
            self._next_reader_id += 1
            self._readers[reader_id] = offset
            self._cv.notify_all()
            return reader_id

    def unregister(self, reader_id: int) -> None:
        with self._cv:
            self._readers.pop(reader_id, None)
            self._cv.notify_all()

    def write(self, data: bytes, timeout: float | None = None) -> None:
        """Write frame-aligned data, blocking if an active reader pins old bytes.

        Supply bounded chunks from the CD reader (e.g. 58,800 bytes). A timeout
        raises BufferTimeout rather than consuming extra memory or dropping audio.
        """
        if len(data) % PCM_FRAME_BYTES:
            raise ValueError("PCM write must end at a frame boundary")
        deadline = None if timeout is None else time.monotonic() + timeout
        position = 0
        with self._cv:
            while position < len(data):
                if self._finished:
                    raise BufferClosed("buffer already finished")
                free = self.capacity - (self._end - self._start)
                if free == 0:
                    # Evict only when space is REQUIRED, preserving replay history.
                    # An active reader pins all data at or after its cursor.
                    evict_limit = min(self._readers.values()) if self._readers else self._end
                    evictable = evict_limit - self._start
                    if evictable > 0:
                        # Free enough space for this bounded write, but do not
                        # eagerly advance past the bytes actually needed.
                        needed = min(len(data) - position, self.capacity)
                        self._start += min(evictable, needed)
                        free = self.capacity - (self._end - self._start)
                    else:
                        remaining = None if deadline is None else deadline - time.monotonic()
                        if remaining is not None and remaining <= 0:
                            raise BufferTimeout("ring full: slow or stalled reader")
                        self._producer_wait_count += 1
                        wait_start = time.monotonic()
                        try:
                            self._cv.wait(remaining)
                        finally:
                            self._producer_wait_seconds += time.monotonic() - wait_start
                        continue
                amount = min(len(data)-position, free)
                amount -= amount % PCM_FRAME_BYTES
                ring_pos = self._end % self.capacity
                first = min(amount, self.capacity-ring_pos)
                self._buf[ring_pos:ring_pos+first] = memoryview(data)[position:position+first]
                if first < amount:
                    self._buf[0:amount-first] = memoryview(data)[position+first:position+amount]
                self._end += amount
                position += amount
                self._cv.notify_all()

    def wait_ahead(self, reader_id: int, min_bytes: int, timeout: float | None = None) -> int:
        """Wait for a minimum number of unread bytes without copying PCM.

        This does not advance the reader. A finished stream with fewer than
        min_bytes remaining is allowed to continue with the remainder, but
        a failed producer raises BufferClosed rather than advertising 200.
        """
        if type(min_bytes) is not int or min_bytes < 4 or min_bytes % 4:
            raise ValueError("min_bytes must be a positive multiple of four")
        if min_bytes > self.capacity:
            raise ValueError("prebuffer exceeds ring capacity")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                if reader_id not in self._readers:
                    raise ValueError("unknown reader")
                offset = self._readers[reader_id]
                if offset < self._start:
                    raise DataExpired("reader data expired")
                available = self._end - offset
                if self._finished:
                    if self._error:
                        raise BufferClosed(self._error)
                    return available
                if available >= min_bytes:
                    return available
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise BufferTimeout("waiting for initial PCM prebuffer")
                self._consumer_wait_count += 1
                started = time.monotonic()
                try:
                    self._cv.wait(remaining)
                finally:
                    elapsed = time.monotonic() - started
                    self._consumer_wait_seconds += elapsed
                    self._consumer_wait_max_seconds = max(self._consumer_wait_max_seconds, elapsed)

    def read(self, reader_id: int, max_bytes: int, timeout: float | None = None) -> bytes:
        if type(max_bytes) is not int or max_bytes < PCM_FRAME_BYTES or max_bytes % PCM_FRAME_BYTES:
            raise ValueError("max_bytes must be a positive multiple of four")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                if reader_id not in self._readers:
                    raise ValueError("unknown reader")
                offset = self._readers[reader_id]
                if offset < self._start:
                    raise DataExpired("reader fell behind oldest retained data")
                available = self._end - offset
                if available:
                    amount = min(available, max_bytes)
                    ring_pos = offset % self.capacity
                    first = min(amount, self.capacity-ring_pos)
                    result = bytes(self._buf[ring_pos:ring_pos+first])
                    if first < amount:
                        result += bytes(self._buf[0:amount-first])
                    self._readers[reader_id] += amount
                    self._cv.notify_all()
                    return result
                if self._finished:
                    if self._error:
                        raise BufferClosed(self._error)
                    return b""  # clean EOF
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise BufferTimeout("waiting for PCM")
                self._consumer_wait_count += 1
                wait_start = time.monotonic()
                try:
                    self._cv.wait(remaining)
                finally:
                    elapsed = time.monotonic() - wait_start
                    self._consumer_wait_seconds += elapsed
                    self._consumer_wait_max_seconds = max(self._consumer_wait_max_seconds, elapsed)

    def finish(self, error: str | None = None) -> None:
        with self._cv:
            self._finished = True
            self._error = error
            self._cv.notify_all()
