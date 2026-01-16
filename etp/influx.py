"""
Async InfluxDB Line Protocol writer helpers.
"""

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Union, List, Tuple

import aiohttp


@dataclass(frozen=True)
class InfluxTargetV2:
    """Configuration for InfluxDB v2 write endpoint."""

    base_url: str  # e.g. "http://localhost:8086"
    org: str
    bucket: str
    token: str
    precision: str = "ns"  # "ns", "us", "ms", "s"


@dataclass(frozen=True)
class InfluxTargetV3:
    """Configuration for InfluxDB v3 write endpoint."""

    base_url: str  # e.g. "http://localhost:8181"
    db: str
    token: str


class AsyncInfluxWriter:
    """
    Async, batched Line Protocol writer.

    Supports:
      - InfluxDB v2 write endpoint: /api/v2/write
      - InfluxDB v3 write_lp endpoint: /api/v3/write_lp
    """

    def __init__(
        self,
        *,
        target_v2: Optional[InfluxTargetV2] = None,
        target_v3: Optional[InfluxTargetV3] = None,
        batch_max_points: int = 10_000,
        flush_interval_s: float = 1.0,
        queue_maxsize: int = 200_000,
        request_timeout_s: float = 10.0,
        max_retries: int = 8,
    ):
        """Initialize the writer with target selection and batching settings."""
        if (target_v2 is None) == (target_v3 is None):
            raise ValueError("Provide exactly one of target_v2 or target_v3")
        self._t2 = target_v2
        self._t3 = target_v3

        self._batch_max_points = batch_max_points
        self._flush_interval_s = flush_interval_s
        self._max_retries = max_retries

        self._q = asyncio.Queue(maxsize=queue_maxsize)
        self._stop = asyncio.Event()
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background flushing task."""
        if self._session is not None:
            raise RuntimeError("AsyncInfluxWriter already started")
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        """Flush remaining data and close the HTTP session."""
        self._stop.set()
        if self._task:
            await self._task
        if self._session:
            await self._session.close()
        self._task = None
        self._session = None

    async def write_lp(self, line: Union[str, bytes]) -> None:
        """
        Enqueue a single Line Protocol record.

        For idempotent retries, include a timestamp in each record.
        """
        if isinstance(line, str):
            payload = line.encode("utf-8")
        else:
            payload = line
        await self._q.put(payload)

    def _endpoint_and_headers(self) -> Tuple[str, dict]:
        """Return the write endpoint URL and headers for the target."""
        if self._t2:
            url = (
                f"{self._t2.base_url}/api/v2/write"
                f"?org={self._t2.org}&bucket={self._t2.bucket}&precision={self._t2.precision}"
            )
            headers = {
                "Authorization": f"Token {self._t2.token}",
                "Content-Type": "text/plain; charset=utf-8",
            }
            return url, headers
        url = f"{self._t3.base_url}/api/v3/write_lp?db={self._t3.db}"
        headers = {
            "Authorization": f"Bearer {self._t3.token}",
            "Content-Type": "text/plain; charset=utf-8",
        }
        return url, headers

    async def _post_with_retries(self, body: bytes) -> None:
        """POST the payload with retryable errors backed off."""
        if self._session is None:
            raise RuntimeError("AsyncInfluxWriter not started")
        url, headers = self._endpoint_and_headers()

        delay = 0.25
        for attempt in range(self._max_retries + 1):
            try:
                async with self._session.post(url, data=body, headers=headers) as response:
                    if 200 <= response.status < 300:
                        return
                    text = await response.text()
                    if response.status in (429,) or 500 <= response.status < 600:
                        raise RuntimeError(f"retryable HTTP {response.status}: {text[:300]}")
                    raise RuntimeError(f"non-retryable HTTP {response.status}: {text[:300]}")
            except Exception:
                if attempt >= self._max_retries:
                    raise
                await asyncio.sleep(delay + random.random() * 0.25)
                delay = min(delay * 2, 8.0)

    async def _run(self) -> None:
        """Drain the queue into batched HTTP writes."""
        buf: List[bytes] = []
        last_flush = time.monotonic()

        async def flush():
            nonlocal buf, last_flush
            if not buf:
                return
            body = b"\n".join(buf) + b"\n"
            buf = []
            last_flush = time.monotonic()
            await self._post_with_retries(body)

        while True:
            if self._stop.is_set() and self._q.empty():
                break

            timeout = max(0.0, self._flush_interval_s - (time.monotonic() - last_flush))
            try:
                item = await asyncio.wait_for(self._q.get(), timeout=timeout)
                buf.append(item)
                if len(buf) >= self._batch_max_points:
                    await flush()
            except asyncio.TimeoutError:
                await flush()

        await flush()
