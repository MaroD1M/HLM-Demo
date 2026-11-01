"""Standalone APScheduler process for HLM-Demo cron jobs."""

#!/usr/bin/env python3
import signal
import time

from app import app, init_app

_stop = False


def _handle_signal(_signum, _frame):
    global _stop
    _stop = True


def main():
    init_app()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    app.logger.info('scheduler process started')
    while not _stop:
        time.sleep(5)
    app.logger.info('scheduler process stopped')


if __name__ == '__main__':
    main()
