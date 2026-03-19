from core.voice import VoiceManager
import sys
import threading
from core.iris_agent import Iris

def main():
    print("Starting Project Iris voice integration...")
    print("Loading local Whisper model...")
    vm = VoiceManager()
    iris = Iris()
    iris.vm = vm
    
    idle_thread = threading.Thread(target=iris.idle_loop, daemon=True)
    idle_thread.start()
    
    print("\n--- Project Iris Wake-up Loop ---")
    print("Press Ctrl+C to terminate.")
    
    try:
        while True:
            # Record from microphone and transcribe
            text = vm.listen()

            # Skip silently if nothing was detected (silence gate)
            if not text:
                continue

            iris.reset_idle_timer()

            # Print the transcription
            print(f"User: {text}")

            # Pass to Iris agent
            with iris.lock:
                response_text = iris.chat(text)
                if response_text:
                    iris.is_speaking = True
            
            # Print and speak the response
            print(f"Iris: {response_text}")
            if response_text:
                try:
                    vm.speak(response_text)
                finally:
                    iris.is_speaking = False
            
    except KeyboardInterrupt:
        print("\nExiting voice loop.")
        sys.exit(0)

if __name__ == '__main__':
    main()
