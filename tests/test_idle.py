import time
import threading
from core.iris_agent import Iris

class MockVM:
    def __init__(self):
        self.is_speaking = threading.Event()
        self.abort_flag = threading.Event()
        import queue
        self.speech_queue = queue.Queue()

    def speak(self, text, avatar=None):
        print(f"[Mock TTS] Speaking: {text}")

def test():
    import core.iris_agent
    core.iris_agent.IDLE_TIMEOUT_SECONDS = 1
    
    iris = Iris()
    vm = MockVM()
    iris.vm = vm
    
    print("Starting idle test...")
    idle_thread = threading.Thread(target=iris.idle_loop, daemon=True)
    idle_thread.start()
    
    time.sleep(6)
    print("Test finished.")

if __name__ == '__main__':
    test()
