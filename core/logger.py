import os
import logging
from datetime import datetime

LOG_DIR = "logs"
LOG_FILE = os.path.join(
    LOG_DIR, f"iris_conversation_{datetime.now().strftime('%Y-%m-%d')}.log"
)


def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("IrisLogger")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.INFO)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


logger = setup_logger()


def log_voice_input(text):
    logger.info(f"🎤 VOICE: {text}")


def log_text_input(text):
    logger.info(f"⌨️  TEXT:  {text}")


def log_iris_response(text):
    logger.info(f"🤖 IRIS:  {text}")


def log_caption(text):
    logger.info(f"💬 CAPTION: {text}")


def log_system(msg):
    logger.info(f"⚙️  SYSTEM: {msg}")
