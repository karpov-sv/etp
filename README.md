# ETPathfinder experimental codes

ETP is a small Python package that provides a minimal asyncio daemon base class and a simple command parser for text, SMS-style, or JSON payloads. It is intended for building lightweight networking services and clients.

## Install
```bash
python -m pip install -e .
```

## Usage

### Daemon base class
```python
import asyncio
from etp import Daemon


class EchoServer(Daemon):
    async def handle_incoming(self, reader, writer):
        while not reader.at_eof():
            data = await reader.readline()
            if not data:
                break
            writer.write(data)
            await writer.drain()


async def main():
    server = EchoServer(name="echo")
    await server.listen(host="127.0.0.1", port=7000)
    await server.run()


asyncio.run(main())
```

### Connection tracking & relay example
This extended example tracks connections by name and relays messages between clients.

```python
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

    async def handle_incoming(self, reader, writer):
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
```

Try it with the bundled example at `examples/relay_server.py` and a few `nc` sessions.

### Command parser
```python
from etp import Command

cmd = Command('set key="value with spaces" path=/tmp/dir "arg"')
print(cmd.name)   # set
print(cmd.args)   # ["arg"]
print(cmd.kwargs) # {"key": "value with spaces", "path": "/tmp/dir"}

print(cmd.to_string())  # round-trip to simple format

sms = Command("status;temp=12.5;unit=C;alive", format="sms")
print(sms.to_string("sms"))  # status;temp=12.5;unit=C;alive
```

### Legacy GUI client
```bash
python client.py --help
```

The legacy GUI client is a lightweight Tkinter tool for manual testing against a daemon.
- Connect via the File menu or CLI flags (`--host`, `--port`), then type commands in the entry box.
- Toggle `Options -> Send newline terminator` to switch between newline- and NUL-terminated sends.
- Incoming messages appear in the log; outgoing commands are shown in blue.

## Development
- Run tests: `python -m pytest`
