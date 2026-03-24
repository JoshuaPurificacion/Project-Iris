from core.voice import VoiceManager
from core.iris_agent import Iris
from core.avatar import AvatarWindow
from core.logger import log_system
from skills import omnisense_skill
import sys
import threading
import time

MANUAL_FEED_TRIGGERS = [
    "manual feed",
    "demo feed",
    "feed the cat",
    "feed cat",
    "feed now",
    "dispense food",
    "dispense cat food",
    "activate feeder",
    "trigger feeder",
]


def handle_input(text, iris, avatar):
    iris.reset_idle_timer()
    if iris.vm:
        try:
            iris.vm.flush()
        except Exception as e:
            log_system(f"Flush failed: {e}")
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
            chat_fn=lambda p, save=False, use_tools=False: iris.chat(
                p, save=save, use_tools=use_tools, avatar=avatar
            ),
            speak_fn=lambda t: iris.vm.speak(t, avatar=avatar),
        )
        return

    # Manual feeder path for demos — bypasses context/tool calling so it is reliable.
    if any(phrase in text_lower for phrase in MANUAL_FEED_TRIGGERS):
        omnisense_skill.trigger(
            chat_fn=lambda p, save=False, use_tools=False: iris.chat(
                p,
                save=save,
                use_tools=use_tools,
                avatar=avatar,
            ),
            speak_fn=lambda t: iris.vm.speak(t, avatar=avatar),
            avatar=avatar,
        )
        return

    # Quiz trigger keywords — start quiz directly (fallback when LLM doesn't call tool)
    QUIZ_TRIGGERS = [
        "quiz me",
        "start quiz",
        "test me",
        "ask me questions",
        "quiz iris",
        "start the quiz",
        "quiz time",
        "let's quiz",
        "give me a quiz",
        "i want a quiz",
        "can you quiz me",
        "please quiz me",
        "quiz me please",
        "wanna quiz",
        "want to be quizzed",
    ]
    if any(phrase in text_lower for phrase in QUIZ_TRIGGERS):
        if not (iris.active_quiz and iris.active_quiz.active):
            from skills.quiz_skill import QuizSession

            iris.active_quiz = QuizSession(avatar=avatar)
            iris.active_quiz.start(
                chat_fn=lambda p, save=False, use_tools=False: iris.chat(
                    p, save=save, use_tools=use_tools, avatar=avatar
                ),
                speak_fn=lambda t: None,  # chat already streams/speaks the question
                topic=None,
            )
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
            target=handle_input, args=(text, iris, avatar), daemon=True
        ).start()

    avatar.on_text_input = on_text_submitted

    # --- Pre-warm LLM in background to avoid blocking ---
    print("Pre-warming LLM into VRAM...")
    threading.Thread(
        target=lambda: iris.chat(
            "System init.", save=False, use_tools=False, avatar=None
        ),
        daemon=True,
    ).start()

    # Give the pre-warm a moment to start before launching GUI
    time.sleep(0.5)

    # --- Idle loop thread ---
    idle_thread = threading.Thread(target=iris.idle_loop, args=(avatar,), daemon=True)
    idle_thread.start()

    # --- Blink loop thread ---
    blink_thread = threading.Thread(target=avatar.blink_loop, daemon=True)
    blink_thread.start()

    # --- Voice loop thread ---
    voice_thread = threading.Thread(
        target=voice_loop, args=(vm, iris, avatar), daemon=True
    )
    voice_thread.start()

    # --- Block main thread on tkinter event loop ---
    avatar.run()


if __name__ == "__main__":
    main()
