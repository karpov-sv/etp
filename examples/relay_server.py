#!/usr/bin/env python3

import argparse
import asyncio

from etp import Daemon


class RelayServer(Daemon):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.by_name = {}

    def on_connect(self, connection):
        connection.state["name"] = None

    def on_disconnect(self, connection):
        name = connection.state.get("name")
        if name and self.by_name.get(name) is connection:
            del self.by_name[name]
            self.broadcast(f"* {name} left\n".encode("utf-8"))

    async def handle_connection(self, reader, writer, incoming):
        del incoming
        writer.write(b"Enter name: ")
        await writer.drain()

        raw = await reader.readline()
        name = raw.decode("utf-8", errors="replace").strip()
        if not name:
            return
        if name in self.by_name:
            writer.write(b"Name already in use.\n")
            await writer.drain()
            return

        connection = self.get_connection(writer)
        if connection:
            connection.state["name"] = name
            self.by_name[name] = connection

        self.send(writer, f"* Welcome {name}\n".encode("utf-8"))
        self.broadcast(f"* {name} joined\n".encode("utf-8"), exclude=writer)

        while not reader.at_eof():
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            if text.startswith("@"):
                target_name, _, message = text[1:].partition(" ")
                if not target_name:
                    continue
                target = self.by_name.get(target_name)
                if target:
                    self.send(target, f"[pm from {name}] {message}\n".encode("utf-8"))
                else:
                    self.send(writer, f"* unknown user {target_name}\n".encode("utf-8"))
            else:
                self.broadcast(f"{name}: {text}\n".encode("utf-8"), exclude=writer)


async def main():
    parser = argparse.ArgumentParser(description="Relay server example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    args = parser.parse_args()

    server = RelayServer(name="relay")
    await server.listen(host=args.host, port=args.port)
    print(f"Listening on {args.host}:{args.port}")
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
