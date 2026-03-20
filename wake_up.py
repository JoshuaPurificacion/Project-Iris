from core.voice import VoiceManager
from core.iris_agent import Iris
from core.avatar import AvatarWindow
import sys
import threading

def handle_input(text, iris, avatar):
    iris.reset_idle_timer()
    text_lower = text.lower()
    
    # Check stop quiz first
    if any(phrase in text.lower() for phrase in ["stop quiz", "end quiz", "quit quiz"]):
        if iris.active_quiz and iris.active_quiz.active:
            iris.active_quiz.end(lambda t: iris.vm.speak(t, avatar=avatar))
            iris.active_quiz = None
            return
            
    # Route to quiz if active
    if iris.active_quiz and iris.active_quiz.active:
        iris.active_quiz.answer(
            text, 
            chat_fn=lambda p, save=False, use_tools=False: iris.chat(p, save=save, use_tools=use_tools, avatar=avatar),
            speak_fn=lambda t: None
        )
        return
        
    # --- Context switching via voice command ---
    if "feed mode" in text_lower or "omni" in text_lower:
        iris.set_context("omnisense")
        iris.vm.speak("Switching to OmniSense mode. Feeder is now active.", avatar=avatar)
        return
    elif "idle mode" in text_lower:
        iris.set_context("idle")
        iris.vm.speak("Switching to idle mode. Hardware tools disabled.", avatar=avatar)
        return

    # Normal conversation
    iris.chat(text, avatar=avatar)

def voice_loop(vm, iris, avatar):
    """Voice capture + Iris response loop — runs on a daemon thread."""
    print("\n--- Project Iris Wake-up Loop ---")
    print("Press Ctrl+C to terminate.")

    try:
        while True:
            # Record from microphone and transcribe
            text = vm.listen(avatar)

            # Skip silently if nothing was detected (silence gate)
            if not text:
                continue

            # Print the transcription
            print(f"User: {text}")

            handle_input(text, iris, avatar)

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

    # --- Text input callback ---
    def on_text_submitted(text):
        print(f"[Text Input] User: {text}")
        threading.Thread(
            target=handle_input,
            args=(text, iris, avatar),
            daemon=True
        ).start()

    avatar.on_text_input = on_text_submitted

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
