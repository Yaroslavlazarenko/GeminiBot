import logging

class AiohttpBadHttpMessageFilter(logging.Filter):
    """Filter out noise from random internet scanners hitting the HTTP admin port."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("aiohttp.server"):
            msg = record.getMessage()
            if "BadHttpMessage" in msg or "400, message" in msg:
                return False
            if record.exc_info and record.exc_info[0] is not None:
                exc_type_name = getattr(record.exc_info[0], "__name__", "")
                if "BadHttpMessage" in exc_type_name:
                    return False
        return True

def setup_logging():
    # Create a logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Create formatters
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(AiohttpBadHttpMessageFilter())

    # Clear existing handlers if any, to avoid duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    # Add handler to logger
    logger.addHandler(console_handler)

    return logger