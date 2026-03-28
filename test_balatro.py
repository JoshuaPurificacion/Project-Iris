"""
Lightweight Balatro smoke test.

Runs the Balatro skill using the HTTP BalatroClient for ~10 seconds, without
spinning up the full GUI. It uses a dummy voice manager so we can see printed
TTS output instead of audio. Run with:

    python test_balatro.py

Stop early with Ctrl+C.
"""

import threading
import time
import queue

from core.logger import log_system
from core.iris_agent import Iris
from skills import balatro_skill


class DummyVM:
    """Minimal VoiceManager stand-in for tests."""

    def __init__(self):
        self.speech_queue = queue.Queue()
        self.is_speaking = threading.Event()
        self.abort_flag = threading.Event()

    def speak(self, text, avatar=None):
        print(f"[DummyTTS] {text}")

    def interrupt_playback(self, avatar=None, reason=None):
        self.abort_flag.set()


def main():
    iris = Iris()
    iris.vm = DummyVM()

    balatro_skill.start_balatro(iris, avatar=None)

    log_system("Runner active. Press Ctrl+C to terminate the session.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_system("Termination signal received. Shutting down...")
    finally:
        balatro_skill.stop_balatro()


if __name__ == "__main__":
    main()
