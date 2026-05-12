"""Tiny HTTP server for the reference self-test contestant.

Routes /play (with any query string) to web/index.html, serves everything else
as static files from web/. python3 -m http.server can't do that, hence this
12-line wrapper.
"""

from __future__ import annotations

import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


class PlayHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/play":
            self.path = "/index.html"
        return super().do_GET()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--root", type=Path, default=Path("web"))
    args = p.parse_args()
    import os
    os.chdir(args.root)
    HTTPServer(("127.0.0.1", args.port), PlayHandler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
