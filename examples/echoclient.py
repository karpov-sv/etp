#!/usr/bin/env python3

import argparse
import asyncio

from etp import Daemon


class EchoClient(Daemon):
    def __init__(self, message, **kwargs):
        super().__init__(**kwargs)
        self.message = message

    async def handle_connection(self, reader, writer, incoming):
        del incoming
        writer.write((self.message + "\n").encode("utf-8"))
        await writer.drain()
        response = await reader.readline()
        if response:
            print(response.decode("utf-8", errors="replace").rstrip())
        self.stop()


async def main():
    parser = argparse.ArgumentParser(description="Echo client example")
    parser.add_argument("message", nargs="?", default="hello")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    args = parser.parse_args()

    client = EchoClient(args.message, name="echo-client")
    await client.connect(args.host, args.port)
    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
