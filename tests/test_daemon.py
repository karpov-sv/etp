import asyncio

from etp.daemon import Daemon


class MemoryTransport(asyncio.Transport):
    def __init__(self, peer_reader, peername=("127.0.0.1", 0), protocol=None):
        self._peer_reader = peer_reader
        self._peername = peername
        self._protocol = protocol
        self._closed = False

    def write(self, data):
        if not self._closed:
            self._peer_reader.feed_data(data)

    def is_closing(self):
        return self._closed

    def close(self):
        if not self._closed:
            self._closed = True
            if self._protocol is not None:
                self._protocol.connection_lost(None)
            self._peer_reader.feed_eof()

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return self._peername
        if name == "socket":
            return None
        return default

    def set_protocol(self, protocol):
        self._protocol = protocol


class HoldDaemon(Daemon):
    async def handle_incoming(self, reader, writer):
        await reader.read()


def make_memory_stream_pair(loop, peername=("127.0.0.1", 0)):
    server_reader = asyncio.StreamReader()
    client_reader = asyncio.StreamReader()

    server_transport = MemoryTransport(client_reader, peername=peername)
    client_transport = MemoryTransport(server_reader, peername=("127.0.0.1", 0))

    server_protocol = asyncio.StreamReaderProtocol(server_reader)
    client_protocol = asyncio.StreamReaderProtocol(client_reader)
    server_protocol.connection_made(server_transport)
    client_protocol.connection_made(client_transport)
    server_transport.set_protocol(server_protocol)
    client_transport.set_protocol(client_protocol)

    server_writer = asyncio.StreamWriter(server_transport, server_protocol, server_reader, loop)
    client_writer = asyncio.StreamWriter(client_transport, client_protocol, client_reader, loop)

    return (server_reader, server_writer), (client_reader, client_writer)


async def start_connection(daemon, peername=("127.0.0.1", 0)):
    loop = asyncio.get_running_loop()
    expected = len(daemon.connections) + 1
    (server_reader, server_writer), (client_reader, client_writer) = make_memory_stream_pair(
        loop, peername=peername
    )
    task = asyncio.create_task(daemon._serve(server_reader, server_writer, incoming=True))
    await wait_for(lambda: len(daemon.connections) >= expected)
    return client_reader, client_writer, task


async def close_connection(task, writer):
    writer.close()
    await asyncio.sleep(0)
    await asyncio.wait_for(task, timeout=1.0)


async def wait_for(predicate, timeout=1.0, interval=0.01):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("Condition not met before timeout")
        await asyncio.sleep(interval)


def test_registry_tracks_connections():
    async def scenario():
        daemon = HoldDaemon()
        _reader, writer, task = await start_connection(daemon, peername=("127.0.0.1", 1111))
        await wait_for(lambda: len(daemon.connections) == 1)

        conn = daemon.connections[0]
        assert conn.peername == ("127.0.0.1", 1111)
        assert conn.incoming is True

        await close_connection(task, writer)
        await wait_for(lambda: len(daemon.connections) == 0)

    asyncio.run(scenario())


def test_send_helper_writes_to_target():
    async def scenario():
        daemon = HoldDaemon()
        reader, writer, task = await start_connection(daemon)
        await wait_for(lambda: len(daemon.connections) == 1)

        conn = daemon.connections[0]
        assert daemon.send(conn, b"ping\n") is True

        line = await reader.readuntil(b"\n")
        assert line == b"ping\n"

        await close_connection(task, writer)

    asyncio.run(scenario())


def test_broadcast_helper_writes_to_all():
    async def scenario():
        daemon = HoldDaemon()
        reader1, writer1, task1 = await start_connection(daemon, peername=("127.0.0.1", 1))
        reader2, writer2, task2 = await start_connection(daemon, peername=("127.0.0.1", 2))
        await wait_for(lambda: len(daemon.connections) == 2)

        daemon.broadcast(b"hello\n")

        line1 = await reader1.readuntil(b"\n")
        line2 = await reader2.readuntil(b"\n")

        assert line1 == b"hello\n"
        assert line2 == b"hello\n"

        await close_connection(task1, writer1)
        await close_connection(task2, writer2)

    asyncio.run(scenario())
