"""App entry point."""

from __future__ import annotations

import logging
import os
import signal
import threading

from .config import load_config
from .http_api import create_server
from .sdk_backend import HcNetSdkBackend

LOGGER = logging.getLogger(__name__)


def main() -> None:
    options_path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    config = load_config(options_path)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, config.log_level.upper()),
    )

    backend = HcNetSdkBackend(config)
    server = create_server("0.0.0.0", config.listen_port, config, backend)

    def stop_server(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %d; stopping", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)

    LOGGER.info("Control API listening on 0.0.0.0:%d", config.listen_port)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        backend.close()


if __name__ == "__main__":
    main()
