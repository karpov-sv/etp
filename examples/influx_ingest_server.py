#!/usr/bin/env python3

import argparse
import asyncio

from etp import Daemon
from etp.influx import AsyncInfluxWriter, InfluxTargetV2, InfluxTargetV3


class InfluxIngestServer(Daemon):
    def __init__(self, writer, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer

    async def on_start(self):
        await self.writer.start()

    async def run(self):
        try:
            await super().run()
        finally:
            await self.writer.close()

    async def handle_incoming(self, reader, writer):
        del writer
        while not reader.at_eof():
            line = await reader.readline()
            if not line:
                break
            payload = line.rstrip(b"\r\n")
            if not payload:
                continue
            await self.writer.write_lp(payload)


def _build_writer(args):
    if args.v2:
        if not args.org or not args.bucket:
            raise ValueError("--org and --bucket are required for --v2")
        target = InfluxTargetV2(
            base_url=args.base_url,
            org=args.org,
            bucket=args.bucket,
            token=args.token,
            precision=args.precision,
        )
        return AsyncInfluxWriter(
            target_v2=target,
            batch_max_points=args.batch_max_points,
            flush_interval_s=args.flush_interval,
        )
    if args.v3:
        if not args.db:
            raise ValueError("--db is required for --v3")
        target = InfluxTargetV3(
            base_url=args.base_url,
            db=args.db,
            token=args.token,
        )
        return AsyncInfluxWriter(
            target_v3=target,
            batch_max_points=args.batch_max_points,
            flush_interval_s=args.flush_interval,
        )
    raise ValueError("Select exactly one of --v2 or --v3")


async def main():
    parser = argparse.ArgumentParser(description="InfluxDB ingest server example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7003)
    parser.add_argument("--base-url", required=True, help="Influx base URL, e.g. http://localhost:8086")
    parser.add_argument("--token", required=True, help="Influx auth token")
    parser.add_argument("--v2", action="store_true", help="Use InfluxDB v2 write endpoint")
    parser.add_argument("--v3", action="store_true", help="Use InfluxDB v3 write_lp endpoint")
    parser.add_argument("--org", help="InfluxDB v2 organization")
    parser.add_argument("--bucket", help="InfluxDB v2 bucket")
    parser.add_argument("--precision", default="ns", help="InfluxDB v2 precision (ns, us, ms, s)")
    parser.add_argument("--db", help="InfluxDB v3 database")
    parser.add_argument("--batch-max-points", type=int, default=5000)
    parser.add_argument("--flush-interval", type=float, default=1.0)
    args = parser.parse_args()

    writer = _build_writer(args)
    server = InfluxIngestServer(writer, name="influx-ingest")
    await server.listen(host=args.host, port=args.port)
    print(f"Listening on {args.host}:{args.port}")
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
