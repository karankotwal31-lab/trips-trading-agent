from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard_export import export_dashboard_snapshot

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


class DashboardHandler(SimpleHTTPRequestHandler):
    """GET/HEAD-only static server for the read-only Trip's dashboard."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None

    def copyfile(self, source, outputfile):
        # Browser refreshes/navigation can close a socket mid-response. Treat that as normal client
        # behavior rather than a server-health failure or noisy traceback.
        try:
            return super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def do_POST(self):
        self.send_error(405, "Trip's dashboard is read-only")

    def do_PUT(self):
        self.send_error(405, "Trip's dashboard is read-only")

    def do_DELETE(self):
        self.send_error(405, "Trip's dashboard is read-only")

    def log_message(self, fmt, *args):
        print(f"dashboard {self.address_string()} - {fmt % args}")


def main():
    ap = argparse.ArgumentParser(description="Serve Trip's read-only verified control center")
    ap.add_argument("--host", default="127.0.0.1", help="bind address; defaults to local-only")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    export_dashboard_snapshot()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Trip's read-only dashboard: http://{args.host}:{args.port}/")
    print("No mutation or execution endpoints are exposed.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
