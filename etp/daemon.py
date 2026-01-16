"""
Minimal asyncio daemon base class.

Provides:
- Incoming TCP server support via asyncio.start_server.
- Outgoing TCP connections with optional reconnect loop.
- Separate handlers for incoming and outgoing connections.
- A connection registry with helpers for cross-connection messaging.

Examples:

Echo server:

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

Echo client:

    import asyncio
    from etp import Daemon

    class EchoClient(Daemon):
        async def handle_outgoing(self, reader, writer):
            writer.write(b"hello\\n")
            await writer.drain()
            response = await reader.readline()
            print(response.decode().rstrip())
            self.stop()

    async def main():
        client = EchoClient(name="echo-client")
        await client.connect("127.0.0.1", 7000)
        await client.run()

    asyncio.run(main())
"""

import asyncio


class Connection:
    """Connection wrapper with metadata and per-connection state."""

    def __init__(self, reader, writer, incoming):
        """Initialize a connection wrapper with reader/writer metadata."""
        self.reader = reader
        self.writer = writer
        self.incoming = incoming
        self.peername = writer.get_extra_info("peername")
        self.state = {}


class Daemon:
    """
    Minimal reusable asyncio daemon.

    Override handle_incoming() and/or handle_outgoing() to implement protocol behavior.
    Use self.state for shared data and self.connections for active Connection objects.
    """

    def __init__(self, name="", state=None):
        """Initialize the daemon with a name and shared state."""
        self.name = name
        self.state = {} if state is None else state
        self.connections = []
        self._server = None
        self._tasks = set()
        self.stop_event = asyncio.Event()

    async def listen(self, host="", port=0):
        """Start listening for incoming connections."""
        self._server = await asyncio.start_server(self._handle_incoming, host=host or None, port=port)
        return self._server

    async def connect(self, host, port, reconnect=False, retry_delay=1.0):
        """Connect to a remote host; optionally keep reconnecting."""
        if reconnect:
            return self._spawn(self._reconnect_loop(host, port, retry_delay))
        reader, writer = await asyncio.open_connection(host=host, port=port)
        return self._spawn(self._serve(reader, writer, incoming=False))

    def stop(self):
        """Signal the daemon to stop."""
        self.stop_event.set()

    async def run(self):
        """Run until stop() is called, then close server and tasks."""
        await self.stop_event.wait()
        await self._shutdown()

    async def handle_incoming(self, reader, writer):
        """
        Override to implement protocol behavior for incoming connections.

        Args:
            reader: asyncio.StreamReader
            writer: asyncio.StreamWriter
        """
        del reader, writer

    async def handle_outgoing(self, reader, writer):
        """
        Override to implement protocol behavior for outgoing connections.

        Args:
            reader: asyncio.StreamReader
            writer: asyncio.StreamWriter
        """
        del reader, writer

    def on_connect(self, connection):
        """Hook called after a connection is registered."""
        del connection

    def on_disconnect(self, connection):
        """Hook called before a connection is unregistered."""
        del connection

    def get_connection(self, writer):
        """Return the Connection object for a StreamWriter, if registered."""
        for conn in self.connections:
            if conn.writer is writer:
                return conn
        return None

    def send(self, target, data):
        """Send raw data to a Connection or StreamWriter."""
        writer = self._resolve_writer(target)
        if writer is None:
            return False
        if isinstance(data, str):
            data = data.encode("ascii", errors="replace")
        if hasattr(writer, "is_closing") and writer.is_closing():
            return False
        writer.write(data)
        return True

    def broadcast(self, data, exclude=None):
        """Send data to all connections, optionally excluding some."""
        exclude_writers = set()
        if exclude is not None:
            if isinstance(exclude, (list, tuple, set)):
                items = exclude
            else:
                items = [exclude]
            for item in items:
                writer = self._resolve_writer(item)
                if writer is not None:
                    exclude_writers.add(writer)
        for conn in list(self.connections):
            if conn.writer in exclude_writers:
                continue
            self.send(conn, data)

    def _spawn(self, coro):
        """Schedule a coroutine as a task and track it."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _handle_incoming(self, reader, writer):
        """Handle an incoming connection from the server listener."""
        self._spawn(self._serve(reader, writer, incoming=True))

    async def _serve(self, reader, writer, incoming):
        """Register a connection, run its handler, and clean up."""
        connection = Connection(reader, writer, incoming)
        self._register(connection)
        try:
            result = self.on_connect(connection)
            if asyncio.iscoroutine(result):
                await result
            if incoming:
                await self.handle_incoming(reader, writer)
            else:
                await self.handle_outgoing(reader, writer)
        finally:
            result = self.on_disconnect(connection)
            if asyncio.iscoroutine(result):
                await result
            self._unregister(connection)
            writer.close()
            await writer.wait_closed()

    def _register(self, connection):
        """Add a Connection to the registry if missing."""
        if connection not in self.connections:
            self.connections.append(connection)

    def _unregister(self, connection):
        """Remove a Connection from the registry if present."""
        if connection in self.connections:
            self.connections.remove(connection)

    def _resolve_writer(self, target):
        """Resolve a Connection or StreamWriter into a StreamWriter."""
        if target is None:
            return None
        if isinstance(target, Connection):
            return target.writer
        return target

    async def _reconnect_loop(self, host, port, retry_delay):
        """Reconnect in a loop until stopped."""
        while not self.stop_event.is_set():
            try:
                reader, writer = await asyncio.open_connection(host=host, port=port)
                await self._serve(reader, writer, incoming=False)
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
                pass
            if not self.stop_event.is_set():
                await asyncio.sleep(retry_delay)

    async def _shutdown(self):
        """Close the server and cancel pending tasks."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
