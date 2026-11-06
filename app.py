"""Serve the OpsWatch dashboard and JSON API on loopback."""

import argparse
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
import re
import time
from urllib.parse import urlsplit

from monitor import Scheduler
from store import Store

ROOT = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
}


def validate_monitor(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a monitor object.")
    name = payload.get("name")
    url = payload.get("url")
    interval = payload.get("interval", 60)
    timeout = payload.get("timeout", 5)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("Enter a name between 1 and 60 characters.")
    if not isinstance(url, str) or len(url) > 500 or not url.isascii() or re.search(r"\s", url):
        raise ValueError("Enter a valid HTTP or HTTPS URL.")
    target = urlsplit(url)
    if (target.scheme not in {"http", "https"} or not target.hostname or
            target.username is not None or target.fragment or target.port == 0):
        raise ValueError("Use an HTTP or HTTPS URL without credentials or a fragment.")
    if type(interval) is not int or not 15 <= interval <= 3600:
        raise ValueError("Check interval must be 15–3600 seconds.")
    if type(timeout) is not int or not 1 <= timeout <= 10:
        raise ValueError("Timeout must be 1–10 seconds.")
    name.encode("utf-8")
    return name.strip(), url, interval, timeout


class Handler(BaseHTTPRequestHandler):
    def parse_request(self):
        if not super().parse_request():
            return False
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.respond(403, {"error": "Open the dashboard from its loopback address."})
            return False
        return True

    def respond(self, status, body, mime="application/json"):
        if mime == "application/json":
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ASSETS:
            name, mime = ASSETS[self.path]
            self.respond(200, (ROOT / "static" / name).read_bytes(), mime)
        elif self.path == "/api/overview":
            self.respond(200, {"demo": self.server.demo, **self.server.store.overview()})
        elif match := re.fullmatch(r"/api/monitors/([1-9][0-9]{0,17})/checks", self.path):
            checks = self.server.store.history(int(match[1]), 100)
            if checks is None:
                self.respond(404, {"error": "Monitor not found."})
            else:
                self.respond(200, checks)
        else:
            self.respond(404, {"error": "Page not found."})

    def can_write(self):
        if self.server.demo:
            self.respond(403, {"error": "The demo is read-only."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
            self.respond(403, {"error": "Open the dashboard from its loopback address."})
            return False
        return True

    def do_POST(self):
        if self.path != "/api/monitors":
            self.respond(404, {"error": "Endpoint not found."})
            return
        if not self.can_write():
            return
        if self.headers.get_content_type() != "application/json":
            self.respond(415, {"error": "Send an application/json request."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4096:
                raise ValueError("Request body must be 1–4096 bytes.")
            values = validate_monitor(json.loads(self.rfile.read(size)))
        except ValueError as error:
            self.respond(400, {"error": str(error)})
            return
        self.respond(201, {"id": self.server.store.create(*values)})

    def do_DELETE(self):
        match = re.fullmatch(r"/api/monitors/([1-9][0-9]{0,17})", self.path)
        if not match:
            self.respond(404, {"error": "Endpoint not found."})
            return
        if self.can_write():
            if self.server.store.delete(int(match[1])):
                self.respond(200, {"deleted": True})
            else:
                self.respond(404, {"error": "Monitor not found."})


def seed_demo(store, now):
    rng = random.Random(41)
    scenarios = [
        ("Customer Portal", {20, 21, 22, 23}, 85),
        ("Payments API", set(range(68, 96)), 125),
        ("Asset CDN", set(), 28),
        ("Account Service", {42, 43, 44}, 64),
    ]
    for name, failures, baseline in scenarios:
        slug = name.lower().replace(" ", "-")
        monitor_id = store.create(name, f"https://{slug}.example/health", 60, 5)
        for index in range(96):
            ok = index not in failures
            store.record(monitor_id, now - (95 - index) * 900, ok, 200 if ok else 503,
                         baseline + rng.randrange(35) if ok else 240, "HTTP 200" if ok else "HTTP 503")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", type=Path, default=ROOT / "opswatch.sqlite3")
    parser.add_argument("--demo", action="store_true", help="Serve a read-only sample dataset")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() if args.demo else nullcontext() as temp:
        store = Store(Path(temp) / "demo.sqlite3" if args.demo else args.db)
        if args.demo:
            seed_demo(store, int(time.time()))
        with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
            server.store = store
            server.demo = args.demo
            with nullcontext() if args.demo else Scheduler(store):
                print(f"OpsWatch: http://127.0.0.1:{args.port}", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass


if __name__ == "__main__":
    main()
