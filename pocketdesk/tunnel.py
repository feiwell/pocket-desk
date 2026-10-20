"""CoreDevice RSD tunnel.

Builds an encrypted tunnel from the phone to its own `remoted` daemon and
returns a live RSD (Remote Service Discovery) connection.

The chain, in order — every step has exactly one working form:

    10.7.0.1:49152          loopback reflected back by LocalDevVPN
      -> RPPairing handshake bare integer wireProtocolVersion
      -> pair-verify         pairing.plist, mode 0600
      -> create_listener()   returns a DYNAMIC port, not 49152
      -> Node TLS-PSK        bridges that dynamic port to a unix socket
      -> CDTX establish      returns the RSD address + client parameters
      -> UserspaceTun        userspace PyTCP stack
      -> connect_tcp()       NOT UserspaceDialPlane
      -> RSD ready           85 services on iOS 27

Deviating from any of the above fails, usually silently. See docs/PITFALLS.md.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import threading
from typing import Any

import pymobiledevice3.remote.tunnel_service as _ts
from pymobiledevice3.remote.remote_service_discovery import (
    RemoteServiceDiscoveryService,
)
from pymobiledevice3.remote.tunnel_service import RemotePairingTcpTunnel
from pymobiledevice3.remote.userspace_tunnel import UserspaceTun

from . import session as _session

DEFAULT_HOST = _session.HOST
TUNNEL_PORT = _session.PORT


def resolve_pairing() -> str | None:
    """Pick a pairing record: $POCKETDESK_PAIRING, then ~/.config, then cwd."""
    return _session.find_pairing_file()


# ---------------------------------------------------------------------------
# PyTCP socket -> asyncio stream adapters
# ---------------------------------------------------------------------------

class _PyTcpReader:
    """Expose PyTCP socket.recv() as a StreamReader."""

    def __init__(self, sock: Any) -> None:
        self._sock = sock
        self._buffer = b""

    async def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            return await self._sock.recv(65536)
        return await self._sock.recv(n)

    async def readexactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = await self._sock.recv(65536)
            if not chunk:
                raise asyncio.IncompleteReadError(self._buffer, n)
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out


class _PyTcpSockShim:
    """ServiceConnection asks the writer for a socket and uses only these."""

    def __init__(self, sock: Any) -> None:
        self._sock = sock

    def setblocking(self, flag: Any) -> None:
        pass

    def settimeout(self, timeout: Any) -> None:
        pass

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class _PyTcpWriter:
    """Expose PyTCP socket.send() as a StreamWriter."""

    def __init__(self, sock: Any) -> None:
        self._sock = sock
        self._pending = b""

    def write(self, data: bytes) -> None:
        self._pending += data

    async def drain(self) -> None:
        if self._pending:
            data, self._pending = self._pending, b""
            await self._sock.send(data)

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        if name == "socket":
            return _PyTcpSockShim(self._sock)
        return default

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass

    async def wait_closed(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Tunnel
# ---------------------------------------------------------------------------

class Tunnel:
    """One CoreDevice session. Use as `async with Tunnel() as t:`."""

    def __init__(self, host: str = DEFAULT_HOST, pairing: str | None = None,
                 verbose: bool = True) -> None:
        self.host = host
        self.pairing = pairing or resolve_pairing()
        self.verbose = verbose
        self.rsd: RemoteServiceDiscoveryService | None = None
        self.ut: UserspaceTun | None = None
        self._tunnel: RemotePairingTcpTunnel | None = None
        self._node: subprocess.Popen | None = None
        self._tasks: list[asyncio.Task] = []

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    async def open(self) -> "Tunnel":
        if not self.pairing:
            raise FileNotFoundError(
                "No pairing record found. Pass --pairing PATH or set "
                "$POCKETDESK_PAIRING."
            )

        record = _session.load_pairing(self.pairing)
        self._log(f"[1] pairing record {record['identifier']}")

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, TUNNEL_PORT), 10
        )
        self._log(f"[2] tcp {self.host}:{TUNNEL_PORT}")

        psk, client_key, server_key = await asyncio.wait_for(
            _session.do_pair_verify(reader, writer, record), 30
        )
        self._log("[3] pair-verify ok")

        listen_port = await asyncio.wait_for(
            _session.create_listener(reader, writer, psk, client_key, server_key), 30
        )
        self._log(f"[4] listener {listen_port}")

        sock_path = self._start_node(psk, listen_port)
        self._log("[5] node TLS-PSK ready")

        relay_reader, relay_writer = await asyncio.open_unix_connection(sock_path)
        self._tunnel = RemotePairingTcpTunnel(relay_reader, relay_writer)
        result = await asyncio.wait_for(self._tunnel.request_tunnel_establish(), 30)
        server_addr = result["serverAddress"]
        server_port = result["serverRSDPort"]
        self._log(f"[6] tunnel {server_addr}:{server_port}")

        _ts.USE_USERSPACE_TUNNEL = True
        self.ut = UserspaceTun()
        self.ut.addr = result["clientParameters"]["address"]
        self.ut.mtu = result["clientParameters"]["mtu"]
        await self.ut.up()
        self.ut.set_peer(server_addr)
        self._tunnel.tun = self.ut

        # Read tasks must be running before dialing, or connect_tcp never completes.
        self._tasks = [
            asyncio.create_task(self._tunnel.sock_read_task()),
            asyncio.create_task(self._tunnel.tun_read_task()),
        ]
        self._log(f"[7] stack up, local {self.ut.addr}")

        self.rsd = RemoteServiceDiscoveryService(
            (server_addr, server_port), open_connection=self._dial
        )
        await asyncio.wait_for(self.rsd.connect(), 45)
        self._log("[8] RSD connected")
        return self

    def _start_node(self, psk: bytes, port: int) -> str:
        tmpdir = tempfile.mkdtemp(prefix="pocketdesk_")
        os.chmod(tmpdir, 0o700)
        sock_path = os.path.join(tmpdir, "relay.sock")

        self._node = subprocess.Popen(
            ["node", _session.get_node_proxy_script(), self.host, str(port), sock_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        assert self._node.stdin
        self._node.stdin.write(psk.hex() + "\n")
        self._node.stdin.flush()

        ready = threading.Event()

        def _pump() -> None:
            for line in self._node.stdout:  # type: ignore[union-attr]
                if "READY" in line:
                    ready.set()
                    return

        threading.Thread(target=_pump, daemon=True).start()
        if not ready.wait(15):
            raise RuntimeError(
                "node TLS-PSK bridge never became ready "
                "(check that node is installed, and that the port is the "
                "listener port, not 49152)"
            )
        return sock_path

    async def _dial(self, host: str, port: int):
        """Connection factory for RSD. Uses connect_tcp, not UserspaceDialPlane."""
        assert self.ut is not None
        sock = await self.ut.connect_tcp(host, port)
        return _PyTcpReader(sock), _PyTcpWriter(sock)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self.rsd:
            try:
                await self.rsd.close()
            except Exception:
                pass
        if self.ut:
            try:
                await self.ut.close()
            except Exception:
                pass
        if self._node:
            self._node.terminate()

    async def __aenter__(self) -> "Tunnel":
        return await self.open()

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def services(self) -> dict[str, Any]:
        assert self.rsd is not None
        return dict(self.rsd.peer_info.get("Services", {}))

    async def service(self, name: str):
        assert self.rsd is not None
        return await self.rsd.start_service(name)
