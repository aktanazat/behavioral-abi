#!/usr/bin/env python3
"""Serve the local registry v0 HTTP API."""

from __future__ import annotations

import argparse
from pathlib import Path

from babi.evidence import EvidenceStore
from babi.registry import Registry, make_server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("runs/registry.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    with Registry(args.db, EvidenceStore()) as registry:
        server = make_server(registry, host=args.host, port=args.port)
        print(f"registry listening on http://{args.host}:{server.server_address[1]}", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
