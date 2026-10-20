"""CoreDevice RSD pairing handshake (portable fork).

Adapted from OpenMinis/MinisSkills `coredevice-toolkit/scripts/coredevice_session.py`
(MIT License, Copyright (c) OpenMinis). See NOTICE for full attribution.

Changes from upstream:
  * No hard-coded `/var/minis` or `/root/.cdtk` paths — portable XDG locations.
  * Dropped the `CoreDeviceSession` class: it drives `UserspaceDialPlane`, whose
    AF_UNIX relay deadlocks on some userspace network stacks. `pocketdesk.tunnel`
    dials with `UserspaceTun.connect_tcp()` instead.
"""

from __future__ import annotations

import base64
import json
import os
import plistlib
import shutil
import struct
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HOST = os.environ.get("POCKETDESK_HOST", "10.7.0.1")
PORT = int(os.environ.get("POCKETDESK_PORT", "49152"))

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("POCKETDESK_CONFIG",
                                 Path.home() / ".config" / "pocketdesk")).expanduser()

PAIRING_ENV = "POCKETDESK_PAIRING"


def get_node_proxy_script() -> str:
    """Return the bundled Node TLS-PSK bridge script."""
    path = SCRIPT_DIR / "tls_psk_proxy.js"
    if not path.exists():
        raise FileNotFoundError(f"missing bundled bridge script: {path}")
    return str(path)


def pairing_candidates() -> list[str]:
    """Places we look for a pairing record, in priority order."""
    out: list[str] = []
    env_value = os.environ.get(PAIRING_ENV)
    if env_value:
        out.append(env_value)
    out.append(str(CONFIG_DIR / "pairing.plist"))
    out.append(str(Path.cwd() / "pairing.plist"))
    return out


def find_pairing_file() -> str | None:
    for candidate in pairing_candidates():
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as handle:
                record = plistlib.load(handle)
        except Exception:
            continue
        if isinstance(record, dict) and {"identifier", "private_key", "public_key"} <= record.keys():
            return str(path)
    return None


def load_pairing(pairing_path: str | None = None) -> dict:
    """Load and validate a pairing record, tightening its file mode if possible."""
    target = pairing_path or find_pairing_file()
    if not target or not os.path.exists(target):
        searched = "\n".join(f"  - {p}" for p in pairing_candidates())
        raise FileNotFoundError(
            "No usable pairing record (.plist) found.\n"
            f"Looked in:\n{searched}\n"
            f"Pass --pairing PATH, or set {PAIRING_ENV}=/path/to/pairing.plist."
        )

    try:
        if os.stat(target).st_mode & 0o077:
            os.chmod(target, 0o600)
    except OSError:
        pass

    # Some volumes (app containers, cloud sync) silently ignore chmod. In that case
    # copy the record into a 0700 directory we own before reading it.
    try:
        if os.stat(target).st_mode & 0o077:
            secure_dir = CONFIG_DIR / "private"
            secure_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(secure_dir, 0o700)
            isolated = secure_dir / Path(target).name
            shutil.copyfile(target, isolated)
            os.chmod(isolated, 0o600)
            target = str(isolated)
    except OSError:
        pass

    with open(target, "rb") as handle:
        record = plistlib.load(handle)
    return {
        "path": target,
        "identifier": record["identifier"],
        "private_key": record["private_key"][:32],
        "public_key": record["public_key"][:32],
        "alt_irk": record.get("alt_irk", b""),
    }


def encode_tlv8(entries) -> bytes:
    parts = []
    for tag, data in entries:
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 255]
            parts.append(bytes([tag, len(chunk)]) + chunk)
            offset += len(chunk)
    return b"".join(parts)


def decode_tlv8(data: bytes) -> dict:
    result: dict = {}
    index = 0
    while index < len(data):
        tag = data[index]
        length = data[index + 1]
        value = data[index + 2:index + 2 + length]
        result[tag] = result.get(tag, b"") + value
        index += 2 + length
    return result


async def _send_plain(writer, value, sequence: int) -> None:
    payload = json.dumps({
        "message": {"plain": {"_0": value}},
        "originatedBy": "host",
        "sequenceNumber": sequence,
    }).encode()
    writer.write(b"RPPairing" + struct.pack(">H", len(payload)) + payload)
    await writer.drain()


async def _recv_plain(reader):
    await reader.readexactly(9)  # b"RPPairing"
    length = struct.unpack(">H", await reader.readexactly(2))[0]
    body = json.loads((await reader.readexactly(length)).decode())
    return body["message"]["plain"]["_0"]


async def do_pair_verify(reader, writer, record: dict):
    """Run pair-verify; returns (shared_secret, client_key, server_key).

    The version field must be a bare integer. Wrapping it in `{_0: 19}` makes
    remoted close the connection immediately.
    """
    await _send_plain(writer, {"request": {"_0": {"handshake": {"_0": {
        "hostOptions": {"attemptPairVerify": True},
        "wireProtocolVersion": 19,
    }}}}}, 0)
    await _recv_plain(reader)

    ephemeral = X25519PrivateKey.generate()
    m1 = encode_tlv8([(6, b"\x01"), (3, ephemeral.public_key().public_bytes_raw())])
    await _send_plain(writer, {"event": {"_0": {"pairingData": {"_0": {
        "data": base64.b64encode(m1).decode(),
        "kind": "verifyManualPairing",
        "startNewSession": True,
    }}}}}, 1)

    response = await _recv_plain(reader)
    m2 = decode_tlv8(base64.b64decode(response["event"]["_0"]["pairingData"]["_0"]["data"]))
    device_public = m2[3]
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(device_public))

    session_key = HKDF(
        algorithm=hashes.SHA512(), length=32,
        salt=b"Pair-Verify-Encrypt-Salt",
        info=b"Pair-Verify-Encrypt-Info",
    ).derive(shared)

    identifier = record["identifier"].encode()
    signature = Ed25519PrivateKey.from_private_bytes(record["private_key"]).sign(
        ephemeral.public_key().public_bytes_raw() + identifier + device_public
    )
    inner = encode_tlv8([(1, identifier), (10, signature)])
    cipher3 = ChaCha20Poly1305(session_key).encrypt(
        b"\x00\x00\x00\x00PV-Msg03", inner, b""
    )
    await _send_plain(writer, {"event": {"_0": {"pairingData": {"_0": {
        "data": base64.b64encode(encode_tlv8([(6, b"\x03"), (5, cipher3)])).decode(),
        "kind": "verifyManualPairing",
        "startNewSession": False,
    }}}}}, 2)

    response = await _recv_plain(reader)
    m4 = decode_tlv8(base64.b64decode(response["event"]["_0"]["pairingData"]["_0"]["data"]))
    if m4.get(6) != b"\x04":
        raise RuntimeError("pair-verify failed: device rejected the pairing record")

    client_key = HKDF(hashes.SHA512(), 32, None, b"ClientEncrypt-main").derive(shared)
    server_key = HKDF(hashes.SHA512(), 32, None, b"ServerEncrypt-main").derive(shared)
    return shared, client_key, server_key


async def create_listener(reader, writer, psk, client_key, server_key) -> int:
    """Ask remoted to open a tunnel listener; returns its port."""
    request = {"request": {"_0": {"createListener": {
        "key": base64.b64encode(psk).decode(),
        "transportProtocolType": "tcp",
        "peerConnectionsInfo": [{
            "owningPID": os.getpid(),
            "owningProcessName": "pocketdesk",
        }],
    }}}}
    nonce = struct.pack("<Q", 0) + b"\x00\x00\x00\x00"
    cipher = ChaCha20Poly1305(client_key).encrypt(nonce, json.dumps(request).encode(), b"")
    envelope = {
        "message": {"streamEncrypted": {"_0": base64.b64encode(cipher).decode()}},
        "originatedBy": "host",
        "sequenceNumber": 3,
    }
    payload = json.dumps(envelope).encode()
    writer.write(b"RPPairing" + struct.pack(">H", len(payload)) + payload)
    await writer.drain()

    await reader.readexactly(9)
    length = struct.unpack(">H", await reader.readexactly(2))[0]
    response = json.loads((await reader.readexactly(length)).decode())
    encrypted = base64.b64decode(response["message"]["streamEncrypted"]["_0"])
    plain = json.loads(ChaCha20Poly1305(server_key).decrypt(nonce, encrypted, b"").decode())
    return plain["response"]["_1"]["createListener"]["port"]
