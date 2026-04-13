"""
fabz- Scheduler — runs the clipping pipeline on a recurring interval.

Usage:
    python scheduler.py                        # use default config.yaml
    python scheduler.py --config myconfig.yaml
    python scheduler.py --run-now              # also run immediately on start
"""

import argparse
import logging
import signal
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from main import run_pipeline
from utils.helpers import load_config, setup_logging

logger = logging.getLogger(__name__)


def _make_job(config_path: str):
    def job():
        logger.info("=== Scheduled pipeline run starting ===")
        try:
            run_pipeline(config_path=config_path)
        except Exception as exc:
            logger.error("Pipeline run failed: %s", exc, exc_info=True)
        logger.info("=== Scheduled pipeline run complete ===")
    return job


def main() -> None:
    parser = argparse.ArgumentParser(description="fabz- Scheduler")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the pipeline once immediately before starting the schedule",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Logging level (DEBUG/INFO/WARNING)"
    )
    args = parser.parse_args()

    setup_logging(args.log_level)

    cfg = load_config(args.config)
    interval_hours = cfg.get("schedule", {}).get("interval_hours", 6)

    logger.info(
        "fabz- scheduler starting. Pipeline will run every %d hour(s).",
        interval_hours,
    )

    if args.run_now:
        logger.info("--run-now: executing pipeline immediately...")
        run_pipeline(config_path=args.config)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        _make_job(args.config),
        trigger=IntervalTrigger(hours=interval_hours),
        id="clipping_pipeline",
        name="Stream Clipping Pipeline",
        replace_existing=True,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum, frame):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "Next run scheduled in %d hour(s). Press Ctrl+C to stop.", interval_hours
    )
    scheduler.start()


if __name__ == "__main__":
    main()
