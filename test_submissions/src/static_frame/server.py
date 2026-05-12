"""Static-frame self-test server. Routes /play → index.html, serves /h264.png and
/h265.png from web/."""

from __future__ import annotations

import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


class PlayHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlparse(self.path).path == "/play":
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


if __name__ == "__main__":
    main()
