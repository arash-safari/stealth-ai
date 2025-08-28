## common/logging_config.py`

import logging

def configure_logging(level=logging.INFO):
    logger = logging.getLogger("plumber-contact-center")
    logger.setLevel(level)
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        ch.setFormatter(fmt)
        logger.addHandler(ch)
