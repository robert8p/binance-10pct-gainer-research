from __future__ import annotations

import logging
import time

from .config import get_settings
from .jobs import process_one, recover_interrupted_jobs

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


def main() -> None:
    settings = get_settings()
    logging.info('Binance 10%% executable-grid research worker started')
    recover_interrupted_jobs(settings)
    while True:
        worked = process_one(settings)
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == '__main__':
    main()
