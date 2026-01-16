#!/usr/bin/env python3

import argparse
import asyncio

from etp import Daemon


class BroadcastServer(Daemon):
    def __init__(self, interval=1.0, **kwargs):
        super().__init__(**kwargs)
        self.interval = interval
        self._tick = 0

    def on_start(self):
        self.start_task(self._ticker())

    async def handle_incoming(self, reader, writer):
        del writer
        await reader.read()

    async def _ticker(self):
        while not self.stop_event.is_set():
            self._tick += 1
            self.broadcast(f"* tick {self._tick}\n".encode("utf-8"))
            await asyncio.sleep(self.interval)


async def main():
    parser = argparse.ArgumentParser(description="Broadcast server example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    server = BroadcastServer(interval=args.interval, name="broadcast")
    await server.listen(host=args.host, port=args.port)
    print(f"Listening on {args.host}:{args.port} (interval={args.interval}s)")
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
