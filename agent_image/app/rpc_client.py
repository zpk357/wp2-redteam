"""Container-local JSON-RPC helper used by the Docker Exec transport."""

from __future__ import annotations

import base64
import os
import struct
import sys
import urllib.error
import urllib.request

MAX_RPC_TRANSPORT_BYTES = 1024 * 1024


def main() -> int:
    if len(sys.argv) > 2:
        print("usage: python -m app.rpc_client [base64-request]", file=sys.stderr)
        return 2
    token = os.environ.get("SANDBOX_TOKEN")
    if not token:
        print("SANDBOX_TOKEN is missing", file=sys.stderr)
        return 3
    try:
        if len(sys.argv) == 2:
            body = base64.urlsafe_b64decode(sys.argv[1].encode("ascii"))
        else:
            header = sys.stdin.buffer.read(8)
            if len(header) != 8:
                print("RPC request length header is incomplete", file=sys.stderr)
                return 5
            expected = struct.unpack(">Q", header)[0]
            if expected > MAX_RPC_TRANSPORT_BYTES:
                print("RPC request exceeded the byte limit", file=sys.stderr)
                return 5
            body = sys.stdin.buffer.read(expected)
            if len(body) != expected:
                print("RPC request body is incomplete", file=sys.stderr)
                return 5
        if len(body) > MAX_RPC_TRANSPORT_BYTES:
            print("RPC request exceeded the byte limit", file=sys.stderr)
            return 5
        request = urllib.request.Request(
            "http://127.0.0.1:8080/rpc",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Protocol-Version": "1",
                "X-Sandbox-Token": token,
            },
        )
        with urllib.request.urlopen(request, timeout=5.0) as response:
            payload = response.read(MAX_RPC_TRANSPORT_BYTES + 1)
            if len(payload) > MAX_RPC_TRANSPORT_BYTES:
                print("RPC response exceeded the byte limit", file=sys.stderr)
                return 5
            sys.stdout.buffer.write(payload)
        return 0
    except (ValueError, urllib.error.URLError) as exc:
        print(f"RPC request failed: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
