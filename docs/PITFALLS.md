# Pitfalls

Things that were tried and did not work, with the symptom that identified each
one. If something here looks like your current plan, it probably is.

Environment where these were established: iPhone18,2 / iOS 27.0.1,
LocalDevVPN, Python 3.12, pymobiledevice3 11.38, Alpine aarch64 userland.

---

## 1. "The TCP connect succeeded, so the reflection works" — no

Some userland network stacks complete the three-way handshake for **any**
address and port. `10.7.0.1:1`, `10.7.0.2:1`, `1.2.3.4:1` and `192.0.2.1:1` all
returned `OPEN 0.00s` on the test device.

`pocketdesk check` therefore binds an ephemeral port, connects to it through
`10.7.0.1`, and requires the exact bytes back. A `refused` on an unused port is
a good sign — it means the path is real and not a black hole.

## 2. Connecting to `127.0.0.1:49152` instead of `10.7.0.1:49152`

`ConnectionResetError` immediately after sending anything, or `EOF` with zero
bytes. remoted only accepts connections that arrive through the VPN
reflection — that is how it fabricates a USB-like identity.

Loopback and LAN-IP connections are actively closed. Setting
`POCKETDESK_HOST=127.0.0.1` to "skip the VPN" does not work.

## 3. Binding a local listener to `127.0.0.1`

Reflected packets arrive with a source address of `10.7.0.1`, so a listener on
`127.0.0.1` never sees them. Bind `0.0.0.0`.

## 4. Treating the failure as a timeout problem

"Connected, sent a request, nothing came back, no error" almost always means the
payload structure is wrong, not that the device is slow. Raising the timeout
just makes it fail slower.

Concrete case: hand-writing `setIconState` timed out three times; the official
`set_icon_state()` returned in under a second.

## 5. Wrapping `wireProtocolVersion` in a typed box

```json
{"wireProtocolVersion": {"_0": 19}}   // connection closed immediately
{"wireProtocolVersion": 19}           // handshake proceeds
```

The field is a bare integer on the RPPairing JSON layer. The `XpcInt64Type`
wrapping belongs to the XPC layer below and must not leak upward.

## 6. "The device advertises protocol 26, we send 19, that must be it"

Sending 26, 19 and 8 produced identical behaviour (`ConnectionResetError`).
The version number was not the cause. Do not spend time there.

## 7. Retrying a failed handshake immediately

remoted throttles sources that fail repeatedly. Symptom: TCP connects fine,
handshake produces **zero** response, and it stays that way for ~30 minutes.
Meanwhile a local echo probe still works, which proves the link is fine.

Rules: ≥30 s between attempts, ≤5 attempts per hour, and once you suspect
backoff, stop completely for 15–30 minutes.

## 8. Pointing the Node bridge at `49152`

`ECONNRESET` from the bridge. The bridge must connect to the port returned by
`createListener` (a random high port, different every session).

## 9. Starting the bridge and returning immediately

If the parent shell exits, the `node` child is reaped and you get
"bridge never became ready". Not a tunnel or VPN problem. Keep the parent alive
for the whole command, or run the process in the foreground.

## 10. `UserspaceDialPlane.dial()` never returning

Tunnel established, stack up, read tasks running, then `dial()` hangs forever
with no exception. Its AF_UNIX socketpair relay does not survive some userland
network stacks.

Fix: bypass it. `UserspaceTun.connect_tcp()` works; wrap the returned PyTCP
socket in a StreamReader/StreamWriter adapter yourself.

## 11. Expecting `UserspaceRsdTunnel` to avoid that

pymobiledevice3 11.38's official `UserspaceRsdTunnel` also dials through
`UserspaceDialPlane`, so it hangs in the same place. "Prefer the official
wrapper" has exactly two exceptions in this project, and this is one of them.

## 12. A stream adapter without `get_extra_info("socket")`

RSD connects, then `AttributeError`. `ServiceConnection` asks the writer for a
socket and calls `setblocking` / `close` / `fileno` on it.

Return a stub object; `fileno()` may return `-1`.

## 13. Not starting the read tasks before dialing

`connect_tcp` will not complete. Start `sock_read_task` and `tun_read_task`
first, then dial.

## 14. Reading `rsd.all_services`

Always empty. The service map lives in `rsd.peer_info["Services"]` (85 entries
on iOS 27).

## 15. Copying old service names from documentation

iOS 26+ names carry `.shim.remote` (and sometimes `.trusted`) suffixes. An
outdated name yields `InvalidServiceError`. Enumerate the real names once and
cache them.

## 16. Assuming `ScreenCaptureService` takes a service handle

It takes the **RSD object**, and needs `await svc.connect()` before
`capture_screenshot()`. Passing the result of `start_service()` raises, and the
method is `capture_screenshot()`, not `capture()`.

(Relevant if you extend this project toward screenshots.)

## 17. Expecting `chmod 600` to work on every volume

On iOS app-container volumes the mode bits are not honoured: `chmod` returns
success and the mode stays `0644`. `ln -s` also fails with
`Operation not permitted`.

`load_pairing()` detects this and copies the record into a `0700` directory it
owns before reading it.

## 18. Installing `pmd-pytcp` without a compiler

No musllinux aarch64 wheel exists, so pip builds it from source and fails
without headers. On Alpine:

```sh
apk add gcc musl-dev make linux-headers openssl-dev python3-dev
pip install --break-system-packages pymobiledevice3 pmd-pytcp cryptography
```

PEP 668 blocks a plain `pip install` into the system Python.

## 19. Concurrent tunnels

remoted's tunnel port is exclusive. Two sessions racing produce one
"bridge never ready" and one timeout, and may push the device into backoff.
Check for stale processes before starting:

```sh
pgrep -af 'pocketdesk|tls_psk_proxy|node .*proxy'
```

---

## Rule of thumb

Four failure signatures carry most of the information:

| Signature | Usually means |
| --- | --- |
| connects, then zero response, no error | payload structure is wrong |
| connects, then immediate RST/EOF | wrong address/port for this service |
| hangs with no exception | a relay layer is deadlocked; bypass it |
| works once, then never again for ~30 min | rate limited; stop retrying |
