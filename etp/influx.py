"""
Async InfluxDB Line Protocol writer helpers.
"""

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Union, List, Tuple, Mapping, Iterable, Any

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None


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


def _escape_key(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def _escape_field_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_items(items: Optional[Iterable[Any]]) -> List[Tuple[str, Any]]:
    if items is None:
        return []
    if isinstance(items, Mapping):
        keys = sorted(items.keys(), key=lambda key: str(key))
        return [(str(key), items[key]) for key in keys]
    return [(str(key), value) for key, value in items]


def _format_field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return f"\"{_escape_field_string(value)}\""
    raise TypeError(f"Unsupported field value type: {type(value).__name__}")


def build_line_protocol(
    measurement: str,
    *,
    tags: Optional[Iterable[Any]] = None,
    fields: Optional[Iterable[Any]] = None,
    timestamp: Optional[Union[int, str]] = None,
) -> str:
    """
    Build a single InfluxDB Line Protocol record.

    Args:
        measurement: Measurement name.
        tags: Mapping or iterable of (key, value) pairs for tags.
        fields: Mapping or iterable of (key, value) pairs for fields.
        timestamp: Optional timestamp (int or str).
    """
    if measurement is None or str(measurement) == "":
        raise ValueError("measurement is required")
    field_items = _normalize_items(fields)
    if not field_items:
        raise ValueError("fields are required")

    measurement_text = _escape_key(str(measurement))
    tag_items = _normalize_items(tags)

    tag_part = ",".join(
        f"{_escape_key(key)}={_escape_key(str(value))}" for key, value in tag_items
    )
    field_part = ",".join(
        f"{_escape_key(key)}={_format_field_value(value)}" for key, value in field_items
    )

    line = measurement_text
    if tag_part:
        line += "," + tag_part
    line += " " + field_part
    if timestamp is not None:
        line += " " + str(timestamp)
    return line


def _unescape(value: str) -> str:
    out = []
    escaped = False
    for ch in value:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def _split_unescaped(value: str, sep: str, *, honor_quotes: bool) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    escaped = False
    in_quotes = False
    for ch in value:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            buf.append(ch)
            continue
        if honor_quotes and ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
            continue
        if ch == sep and not in_quotes:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if escaped:
        buf.append("\\")
    parts.append("".join(buf))
    return parts


def _split_first_unescaped(value: str, sep: str, *, honor_quotes: bool) -> Tuple[str, str]:
    escaped = False
    in_quotes = False
    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if honor_quotes and ch == '"':
            in_quotes = not in_quotes
            continue
        if ch == sep and not in_quotes:
            return value[:idx], value[idx + 1 :]
    raise ValueError(f"Missing separator {sep!r} in {value!r}")


def _split_fields_and_timestamp(value: str) -> Tuple[str, Optional[str]]:
    escaped = False
    in_quotes = False
    last_space = -1
    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch == " " and not in_quotes:
            last_space = idx
    if last_space == -1:
        return value, None
    return value[:last_space], value[last_space + 1 :]


def _parse_field_value(value: str) -> Any:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return _unescape(value[1:-1])
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.endswith("i"):
        number = value[:-1]
        if number and (number.isdigit() or (number[0] == "-" and number[1:].isdigit())):
            return int(number)
    try:
        return float(value)
    except ValueError:
        return value


def parse_line_protocol(
    line: Union[str, bytes],
) -> Tuple[str, dict, dict, Optional[Union[int, str]]]:
    """
    Parse a single Line Protocol record into measurement, tags, fields, and timestamp.
    """
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = line
    text = text.rstrip("\r\n")
    if not text:
        raise ValueError("line protocol payload is empty")

    measurement_part, rest = _split_first_unescaped(text, " ", honor_quotes=False)
    rest = rest.strip()
    if not rest:
        raise ValueError("line protocol payload missing fields")

    fields_part, timestamp_part = _split_fields_and_timestamp(rest)
    fields_part = fields_part.strip()
    if not fields_part:
        raise ValueError("line protocol payload missing fields")

    measurement_tokens = _split_unescaped(measurement_part, ",", honor_quotes=False)
    measurement = _unescape(measurement_tokens[0])
    tags = {}
    for token in measurement_tokens[1:]:
        if not token:
            continue
        key, value = _split_first_unescaped(token, "=", honor_quotes=False)
        tags[_unescape(key)] = _unescape(value)

    fields = {}
    field_tokens = _split_unescaped(fields_part, ",", honor_quotes=True)
    for token in field_tokens:
        if not token:
            continue
        key, value = _split_first_unescaped(token, "=", honor_quotes=True)
        fields[_unescape(key)] = _parse_field_value(value)

    timestamp: Optional[Union[int, str]] = None
    if timestamp_part:
        timestamp_part = timestamp_part.strip()
        if timestamp_part:
            try:
                timestamp = int(timestamp_part)
            except ValueError:
                timestamp = timestamp_part

    return measurement, tags, fields, timestamp


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
        debug: bool = False,
    ):
        """Initialize the writer with target selection and batching settings."""
        if (target_v2 is None) == (target_v3 is None):
            raise ValueError("Provide exactly one of target_v2 or target_v3")
        self._t2 = target_v2
        self._t3 = target_v3

        self._batch_max_points = batch_max_points
        self._flush_interval_s = flush_interval_s
        self._max_retries = max_retries
        self._request_timeout_s = request_timeout_s
        self.debug = debug

        self._q = asyncio.Queue(maxsize=queue_maxsize)
        self._stop = asyncio.Event()
        self._session: Optional[object] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background flushing task."""
        if self._session is not None:
            raise RuntimeError("AsyncInfluxWriter already started")
        if aiohttp is None:
            raise RuntimeError("aiohttp is required; install with etp[influx]")
        timeout = aiohttp.ClientTimeout(total=self._request_timeout_s)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._task = asyncio.create_task(self._run())
        self._debug("writer started")

    async def close(self, drain: bool = True) -> None:
        """Flush remaining data (unless drain=False) and close the HTTP session."""
        self._stop.set()
        if self._task:
            if drain:
                await self._task
            else:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        if self._session:
            await self._session.close()
        self._task = None
        self._session = None
        self._debug(f"writer closed drain={drain}")

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
                        self._debug(f"write ok status={response.status} bytes={len(body)}")
                        return
                    text = await response.text()
                    if response.status in (429,) or 500 <= response.status < 600:
                        raise RuntimeError(f"retryable HTTP {response.status}: {text[:300]}")
                    raise RuntimeError(f"non-retryable HTTP {response.status}: {text[:300]}")
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= self._max_retries:
                    self._debug("write failed after retries")
                    raise
                self._debug(f"write retry attempt={attempt + 1}")
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
            self._debug(f"flush points={len(buf)}")
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

    def _debug(self, message: str) -> None:
        """Print a timestamped debug message when debug is enabled."""
        if not self.debug:
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] influx {message}")
