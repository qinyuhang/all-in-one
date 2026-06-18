#!/usr/bin/env python3

import hashlib
import hmac
import json
import os
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path


SOCKET_PATH = Path("/srv/blog/.webhook/blog.sock")
TRIGGER_PATH = Path("/srv/blog/.deploy-trigger")
EXPECTED_REPOSITORY = "qinyuhang/qinyuhang.github.io"
MAX_BODY_SIZE = 4096
MAX_CLOCK_SKEW = 300


def load_secret():
    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not credentials_directory:
        raise RuntimeError("CREDENTIALS_DIRECTORY is not set")
    secret = (Path(credentials_directory) / "webhook-secret").read_bytes().strip()
    if len(secret) < 32:
        raise RuntimeError("webhook secret must contain at least 32 bytes")
    return secret


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def verify_request(secret, timestamp, signature, body, now=None):
    try:
        timestamp_value = int(timestamp)
    except ValueError as error:
        raise RequestError(401, "invalid signature") from error
    current_time = int(time.time()) if now is None else now
    if abs(current_time - timestamp_value) > MAX_CLOCK_SKEW:
        raise RequestError(401, "expired signature")

    signed_payload = timestamp.encode("ascii") + b"." + body
    expected = "sha256=" + hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RequestError(401, "invalid signature")

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RequestError(400, "invalid json") from error
    if payload.get("repository") != EXPECTED_REPOSITORY:
        raise RequestError(403, "repository rejected")
    commit = payload.get("sha", "")
    if not isinstance(commit, str) or len(commit) != 40:
        raise RequestError(400, "invalid commit")
    try:
        int(commit, 16)
    except ValueError as error:
        raise RequestError(400, "invalid commit") from error
    return payload


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "BlogWebhook/1"

    def do_POST(self):
        if self.path != "/_hooks/blog":
            self.respond(404, "not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(400, "invalid content length")
            return
        if length < 1 or length > MAX_BODY_SIZE:
            self.respond(413, "invalid body size")
            return

        timestamp = self.headers.get("X-Blog-Timestamp", "")
        signature = self.headers.get("X-Blog-Signature", "")
        try:
            verify_request(
                self.server.secret,
                timestamp,
                signature,
                self.rfile.read(length),
            )
        except RequestError as error:
            self.respond(error.status, error.message)
            return

        TRIGGER_PATH.touch(exist_ok=True)
        os.utime(TRIGGER_PATH, None)
        self.respond(202, "deployment queued")

    def do_GET(self):
        self.respond(405, "method not allowed")

    def respond(self, status, message):
        body = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        print(
            f"{self.client_address or 'unix'} {format_string % args}",
            file=sys.stderr,
        )


class WebhookServer(socketserver.UnixStreamServer):
    allow_reuse_address = True

    def __init__(self, socket_path, handler, secret):
        self.secret = secret
        super().__init__(str(socket_path), handler)


def main():
    secret = load_secret()
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    with WebhookServer(SOCKET_PATH, WebhookHandler, secret) as server:
        SOCKET_PATH.chmod(0o666)
        server.serve_forever()


if __name__ == "__main__":
    main()
