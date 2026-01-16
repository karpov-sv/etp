# etp

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
    async def handle_connection(self, reader, writer, incoming):
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
