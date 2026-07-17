import logging
import sys


def setup_logger(name: str) -> logging.Logger:
    """
    Returns a structured telemetry-ready standard python logger.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)

        # Standard structured stdout handler
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
