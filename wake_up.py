from core.voice import VoiceManager
from core.iris_agent import Iris
from core.avatar import AvatarWindow
import sys
import threading

def voice_loop(vm, iris, avatar):
    """Voice capture + Iris response loop — runs on a daemon thread."""
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

            # --- Context switching via voice command ---
            text_lower = text.lower()
            if "feed mode" in text_lower or "omni" in text_lower:
                iris.set_context("omnisense")
                vm.speak("Switching to OmniSense mode. Feeder is now active.", avatar=avatar)
                continue
            elif "idle mode" in text_lower:
                iris.set_context("idle")
                vm.speak("Switching to idle mode. Hardware tools disabled.", avatar=avatar)
                continue

            # Pass to Iris agent (avatar state changes happen inside chat + speak)
            with iris.lock:
                response_text = iris.chat(text, avatar=avatar)
                if response_text:
                    iris.is_speaking = True

            # Print and speak the response
            print(f"Iris: {response_text}")
            if response_text:
                try:
                    vm.speak(response_text, avatar=avatar)
                finally:
                    iris.is_speaking = False

    except KeyboardInterrupt:
        print("\nExiting voice loop.")
        sys.exit(0)


def main():
    print("Starting Project Iris voice integration...")

    # --- Avatar window (must run on main thread) ---
    avatar = AvatarWindow()

    # --- Voice and agent setup ---
    print("Loading local Whisper model...")
    vm = VoiceManager()
    iris = Iris()
    iris.vm = vm

    # --- Idle loop thread ---
    idle_thread = threading.Thread(target=iris.idle_loop, args=(avatar,), daemon=True)
    idle_thread.start()

    # --- Blink loop thread ---
    blink_thread = threading.Thread(target=avatar.blink_loop, daemon=True)
    blink_thread.start()

    # --- Voice loop thread ---
    voice_thread = threading.Thread(target=voice_loop, args=(vm, iris, avatar), daemon=True)
    voice_thread.start()

    # --- Block main thread on tkinter event loop ---
    avatar.run()


if __name__ == '__main__':
    main()
