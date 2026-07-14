from __future__ import annotations

import argparse
from pathlib import Path

from disk_cleanup.web.server import serve_forever


def main() -> int:
    parser = argparse.ArgumentParser(description="disk-cleanup read-only report server")
    parser.add_argument("--db", required=True)
    parser.add_argument("--scan-id", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    serve_forever(
        Path(args.db), args.scan_id, host="127.0.0.1", port=args.port,
        open_browser=not args.no_open, token=args.token, run_id=args.run_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
