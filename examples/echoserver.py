#!/usr/bin/env python3

import argparse
import asyncio

from etp import Daemon


class EchoServer(Daemon):
    async def handle_connection(self, reader, writer, incoming):
        del incoming
        while not reader.at_eof():
            data = await reader.readline()
            if not data:
                break
            print(f"Received: {data}")
            writer.write(data)
            await writer.drain()


async def main():
    parser = argparse.ArgumentParser(description="Echo server example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    args = parser.parse_args()

    server = EchoServer(name="echo")
    await server.listen(host=args.host, port=args.port)
    print(f"Listening on {args.host}:{args.port}")
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
