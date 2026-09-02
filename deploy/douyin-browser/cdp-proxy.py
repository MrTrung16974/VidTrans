#!/usr/bin/env python3
"""HTTP-aware reverse proxy for Chromium's DevTools endpoint.

Two rewrites are applied so that callers in other Docker containers can use
CDP over plain HTTP and WebSocket without knowing the sidecar's internals:

1. **Host header** – Chromium rejects requests whose Host does not match
   127.0.0.1 / localhost (DNS-rebinding protection).  Every inbound request
   gets its Host rewritten to ``127.0.0.1:<target_port>`` before forwarding.

2. **webSocketDebuggerUrl in JSON responses** – /json/version and /json/list
   return a ``webSocketDebuggerUrl`` / ``webSocketDebuggerURL`` that points to
   ``127.0.0.1:<target_port>``, which is unreachable from other containers.
   The proxy rewrites that address to ``<listen_host>:<listen_port>`` (or
   ``<external_host>:<listen_port>`` if provided) so that Playwright (running
   in the backend container) can open the CDP WebSocket through this proxy.

WebSocket upgrades: after the HTTP 101 handshake, both sides switch to raw
bidirectional byte forwarding so the CDP framing is passed through unchanged.

Usage:
    douyin-cdp-proxy <listen_host> <listen_port> <target_host> <target_port> [external_host]
"""
from __future__ import annotations

import logging
import re
import socket
import socketserver
import sys
import threading

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import time
def log_debug(msg):
    try:
        with open("/data/profile/cdp.log", "a") as f:
            f.write(f"[{time.time()}] {msg}\n")
    except:
        pass

LISTEN_HOST = sys.argv[1]
LISTEN_PORT = int(sys.argv[2])
TARGET_HOST = sys.argv[3]
TARGET_PORT = int(sys.argv[4])
EXTERNAL_HOST = sys.argv[5] if len(sys.argv) > 5 else LISTEN_HOST

# Chromium only trusts these Host values on its DevTools port.
REWRITTEN_HOST = f"{TARGET_HOST}:{TARGET_PORT}"

# The address that appears in /json/* responses — must be rewritten so callers
# outside this container can reach the proxy instead of the loopback socket.
_TARGET_WS_PREFIX = f"ws://{TARGET_HOST}:{TARGET_PORT}"
_PROXY_WS_PREFIX = f"ws://{EXTERNAL_HOST}:{LISTEN_PORT}"

# /json/* endpoints for which we rewrite the JSON body.
_JSON_PATHS = re.compile(r"^(?:https?://[^/]+)?/json(?:/(?:version|list|new|activate|close))?(?:\?.*)?$")

# Hop-by-hop headers that must not be forwarded (RFC 7230 §6.1).
HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "Connection",
        "Keep-Alive",
        "Proxy-Authenticate",
        "Proxy-Authorization",
        "TE",
        "Trailer",
        "Transfer-Encoding",
    )
)


def _rewrite_ws_urls(body: bytes) -> bytes:
    """Replace the loopback DevTools WS address with the proxy address."""
    try:
        text = body.decode("utf-8")
        # Replace ws:// explicitly
        rewritten = text.replace(f"ws://{TARGET_HOST}:{TARGET_PORT}", f"ws://{EXTERNAL_HOST}:{LISTEN_PORT}")
        # Replace any other occurrences of target host:port just in case
        rewritten = rewritten.replace(f"{TARGET_HOST}:{TARGET_PORT}", f"{EXTERNAL_HOST}:{LISTEN_PORT}")
        
        if rewritten != text:
            return rewritten.encode("utf-8")
    except Exception:
        pass
    return body


def _forward_raw(source, destination):
    """Copy bytes between two sockets until one side closes."""
    try:
        while chunk := source.recv(65536):
            destination.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class CDPProxyHandler(socketserver.BaseRequestHandler):
    """Rewrite Host + webSocketDebuggerUrl then proxy to Chromium's DevTools."""

    def handle(self):
        client = self.request
        client.settimeout(30)

        # Accumulate bytes until we have the full HTTP request headers.
        raw = b""
        try:
            while b"\r\n\r\n" not in raw:
                chunk = client.recv(4096)
                if not chunk:
                    return
                raw += chunk
        except OSError:
            return

        header_end = raw.index(b"\r\n\r\n")
        header_bytes = raw[:header_end]
        body_prefix = raw[header_end + 4:]

        lines = header_bytes.decode("latin-1", errors="replace").split("\r\n")
        if not lines:
            return
        request_line = lines[0]

        # Determine whether this is a /json/* request (needs body rewrite).
        try:
            req_path = request_line.split(" ", 2)[1]
            needs_body_rewrite = bool(_JSON_PATHS.match(req_path))
        except (IndexError, AttributeError):
            req_path = "unknown"
            needs_body_rewrite = False

        log_debug(f"REQ_LINE: {request_line.strip()} | PATH: {req_path} | REWRITE: {needs_body_rewrite}")

        # Rebuild headers, replacing Host and stripping hop-by-hop entries.
        rewritten = [request_line]
        is_websocket = False
        connection_tokens = []
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            name_stripped = name.strip()
            value_stripped = value.strip()
            lower = name_stripped.lower()
            if lower == "host":
                rewritten.append(f"Host: {REWRITTEN_HOST}")
            elif lower == "connection":
                connection_tokens = [t.strip().lower() for t in value_stripped.split(",")]
                if not needs_body_rewrite:
                    rewritten.append(f"Connection: {value_stripped}")
            elif lower == "upgrade" and value_stripped.lower() == "websocket":
                is_websocket = True
                rewritten.append(f"{name_stripped}: {value_stripped}")
            elif lower not in HOP_BY_HOP or lower in connection_tokens:
                rewritten.append(f"{name_stripped}: {value_stripped}")

        if needs_body_rewrite:
            rewritten.append("Connection: close")

        rebuilt = "\r\n".join(rewritten) + "\r\n\r\n"

        try:
            upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=8)
        except OSError as exc:
            logger.warning("Cannot connect to Chromium DevTools: %s", exc)
            try:
                client.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            return

        try:
            upstream.sendall(rebuilt.encode("latin-1") + body_prefix)
        except OSError:
            upstream.close()
            return

        if is_websocket:
            # Read and relay the upstream HTTP 101 upgrade response.
            response_buf = b""
            upstream.settimeout(10)
            try:
                while b"\r\n\r\n" not in response_buf:
                    chunk = upstream.recv(4096)
                    if not chunk:
                        break
                    response_buf += chunk
            except OSError:
                upstream.close()
                return
            try:
                client.sendall(response_buf)
            except OSError:
                upstream.close()
                return

            # Switch both sockets to raw bidirectional byte forwarding.
            upstream.settimeout(None)
            client.settimeout(None)
            relay = threading.Thread(target=_forward_raw, args=(upstream, client), daemon=True)
            relay.start()
            _forward_raw(client, upstream)
            relay.join(timeout=2)

        elif needs_body_rewrite:
            # Read headers until \r\n\r\n
            upstream.settimeout(5)
            response_raw = b""
            try:
                while b"\r\n\r\n" not in response_raw:
                    chunk = upstream.recv(4096)
                    if not chunk:
                        break
                    response_raw += chunk
            except OSError:
                pass

            if b"\r\n\r\n" in response_raw:
                header_end = response_raw.index(b"\r\n\r\n")
                resp_headers = response_raw[:header_end]
                resp_body = response_raw[header_end + 4:]
                
                # Determine expected body length
                content_length = 0
                header_text = resp_headers.decode("latin-1", errors="replace")
                m = re.search(r"(?im)^Content-Length:\s*(\d+)", header_text)
                if m:
                    content_length = int(m.group(1))

                # Read the rest of the body if needed
                while len(resp_body) < content_length:
                    try:
                        chunk = upstream.recv(4096)
                        if not chunk:
                            break
                        resp_body += chunk
                    except OSError:
                        break

                # Rewrite URLs
                resp_body = _rewrite_ws_urls(resp_body)
                
                log_debug(f"RESP BODY: {resp_body.decode('utf-8')}")
                
                # Update Content-Length header
                new_length = len(resp_body)
                header_text = re.sub(
                    r"(?im)^Content-Length:.*$",
                    f"Content-Length: {new_length}",
                    header_text,
                )
                response_raw = header_text.encode("latin-1") + b"\r\n\r\n" + resp_body

            try:
                client.sendall(response_raw)
            except OSError:
                pass

        else:
            # Plain HTTP passthrough.
            upstream.settimeout(60)
            client.settimeout(60)
            try:
                while chunk := upstream.recv(65536):
                    client.sendall(chunk)
            except OSError:
                pass

        try:
            upstream.close()
        except OSError:
            pass


class ThreadingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with ThreadingProxy((LISTEN_HOST, LISTEN_PORT), CDPProxyHandler) as server:
    server.serve_forever(poll_interval=0.25)
