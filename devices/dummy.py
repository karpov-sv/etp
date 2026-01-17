#!/usr/bin/env python3

"""
Dummy device daemon that emits random temperature readings and ingests to InfluxDB.

Required .env keys (via python-decouple):
- INFLUX_VERSION: v2 or v3 (default: v2)
- INFLUX_BASE_URL
- INFLUX_TOKEN
- INFLUX_ORG, INFLUX_BUCKET, INFLUX_PRECISION (v2 only; precision default: ns)
- INFLUX_DB (v3 only)
"""

import argparse
import asyncio
import random
import time
from typing import Dict, Any

from decouple import config

from etp import Daemon, Command
from etp.influx import AsyncInfluxWriter, InfluxTargetV2, InfluxTargetV3, build_line_protocol


def _parse_value(text: str) -> Any:
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    if lowered.isdigit() or (lowered.startswith("-") and lowered[1:].isdigit()):
        return int(lowered)
    try:
        return float(text)
    except ValueError:
        return text


def _format_value(value: Any) -> str:
    if isinstance(value, str) and any(ch.isspace() for ch in value):
        return '"' + value.replace('"', '\\"') + '"'
    return str(value)


def _load_influx_writer(debug=False) -> AsyncInfluxWriter:
    version = config("INFLUX_VERSION", default="v2").strip().lower()
    base_url = config("INFLUX_BASE_URL")
    token = config("INFLUX_TOKEN")

    if version in ("v2", "2"):
        org = config("INFLUX_ORG")
        bucket = config("INFLUX_BUCKET")
        precision = config("INFLUX_PRECISION", default="ns")
        target = InfluxTargetV2(
            base_url=base_url,
            org=org,
            bucket=bucket,
            token=token,
            precision=precision,
        )
        return AsyncInfluxWriter(target_v2=target, debug=debug)

    if version in ("v3", "3"):
        db = config("INFLUX_DB")
        target = InfluxTargetV3(
            base_url=base_url,
            db=db,
            token=token,
        )
        return AsyncInfluxWriter(target_v3=target, debug=debug)

    raise ValueError("INFLUX_VERSION must be v2 or v3")


def _parse_tags(values):
    tags = {}
    for item in values:
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Tag must be key=value: {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Tag key is required: {item!r}")
        tags[key] = value.strip()
    return tags


class DummyDevice(Daemon):
    def __init__(
        self,
        writer: AsyncInfluxWriter,
        rate: float,
        metric_name: str,
        tags: Dict[str, str],
        **kwargs,
    ):
        """Initialize the dummy device with a writer, rate, metric name, and tags."""
        super().__init__(**kwargs)
        self.writer = writer
        self._drain_on_shutdown = True
        self.metric_name = metric_name
        self.tags = dict(tags)
        self.state.update(
            {
                "rate": rate,
                self.metric_name: None,
                "last_update_ns": None,
                "count": 0,
                "device_id": self.name or "dummy",
            }
        )
        self._state_lock = asyncio.Lock()

    async def on_start(self):
        """Start the Influx writer and background sensor loop."""
        await self.writer.start()
        self.start_task(self._sensor_loop())

    async def run(self):
        """Run the daemon and close the writer on shutdown."""
        try:
            await super().run()
        finally:
            await self.writer.close(drain=self._drain_on_shutdown)

    async def handle_incoming(self, reader, writer):
        """Handle incoming text commands from a connection."""
        async for command in self.iter_commands(reader):
            if not command:
                continue
            try:
                cmd = Command(command)
            except Exception as exc:
                await self.send_line(writer, f"error message={_format_value(str(exc))}")
                continue

            name = (cmd.name or "").strip().lower()
            if name == "exit":
                self._drain_on_shutdown = False
                await self.send_line(writer, "bye")
                self.stop()
                break
            if name == "set":
                await self._handle_set(cmd, writer)
            elif name == "status":
                await self._send_status(writer)
            else:
                await self.send_line(writer, "error message=\"unknown command\"")

    async def _handle_set(self, cmd: Command, writer) -> None:
        """Apply "set" command updates to shared state."""
        if not cmd.kwargs:
            await self.send_line(writer, "error message=\"missing parameters\"")
            return

        updates: Dict[str, Any] = {}
        for key, value in cmd.kwargs.items():
            parsed = _parse_value(value)
            if key == "rate":
                try:
                    parsed_rate = float(parsed)
                except (TypeError, ValueError):
                    await self.send_line(writer, "error message=\"invalid rate\"")
                    return
                if parsed_rate <= 0:
                    await self.send_line(writer, "error message=\"rate must be positive\"")
                    return
                updates[key] = parsed_rate
            else:
                updates[key] = parsed

        async with self._state_lock:
            self.state.update(updates)

        updated = " ".join(f"{key}={_format_value(value)}" for key, value in updates.items())
        await self.send_line(writer, f"ok {updated}".strip())

    async def _send_status(self, writer) -> None:
        """Send the current status snapshot back to the client."""
        async with self._state_lock:
            snapshot = dict(self.state)

        keys = ["device_id", "rate", self.metric_name, "count", "last_update_ns"]
        parts = []
        for key in keys:
            if key not in snapshot:
                continue
            parts.append(f"{key}={_format_value(snapshot[key])}")
        await self.send_line(writer, "status " + " ".join(parts))

    async def _sensor_loop(self):
        """Generate synthetic temperature readings and ingest them."""
        while not self.stop_event.is_set():
            temperature = (self.state.get(self.metric_name) or 20) + random.gauss(0, 0.1)
            now_ns = time.time_ns()

            async with self._state_lock:
                self.state[self.metric_name] = temperature
                self.state["last_update_ns"] = now_ns
                self.state["count"] = int(self.state.get("count", 0)) + 1
                rate = float(self.state.get("rate", 1.0))
                device_id = self.state.get("device_id", self.name or "dummy")

            line = build_line_protocol(
                self.metric_name,
                tags={**{"device": device_id}, **self.tags},
                fields={"value": temperature},
                timestamp=now_ns,
            )
            await self.writer.write_lp(line)

            await asyncio.sleep(min(1/rate, 100))



async def main():
    parser = argparse.ArgumentParser(description="Dummy device daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7004)
    parser.add_argument("--rate", type=float, default=1.0, help="Update rate, readings per second")
    parser.add_argument(
        "--metric-name",
        "--parameter-name",
        dest="metric_name",
        default="temperature",
        help="Metric/parameter name for readings",
    )
    parser.add_argument("--name", default="dummy")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Additional Influx tag key=value (repeatable)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    try:
        extra_tags = _parse_tags(args.tag)
    except ValueError as exc:
        parser.error(str(exc))

    writer = _load_influx_writer(debug=args.debug)
    daemon = DummyDevice(
        writer,
        rate=args.rate,
        metric_name=args.metric_name,
        tags=extra_tags,
        name=args.name,
        debug=args.debug,
    )
    await daemon.listen(host=args.host, port=args.port)
    print(f"Listening on {args.host}:{args.port} (rate={args.rate}s)")
    await daemon.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
