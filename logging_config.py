import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime

def setup_logging():
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')

    # Create a logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)  # Change to INFO

    # Create formatters
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Change to INFO
    console_handler.setFormatter(console_formatter)

    # Create error file handler
    error_handler = RotatingFileHandler(
        f'logs/error_{datetime.now().strftime("%Y%m%d")}.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(error_handler)

    return logger 