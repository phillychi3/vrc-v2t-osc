"""Manual local OSC receiver used by P0/P4 integration checks."""

from __future__ import annotations

import argparse
import logging

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer


def main() -> None:
    parser = argparse.ArgumentParser(description="顯示收到的 OSC 訊息")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9000, type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(
        lambda address, *values: logging.info("%s %r", address, values)
    )
    server = ThreadingOSCUDPServer((args.host, args.port), dispatcher)
    logging.info("OSC receiver listening on %s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
