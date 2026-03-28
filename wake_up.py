from core.voice import VoiceManager
from core.iris_agent import Iris
from core.avatar import AvatarWindow
from core.logger import log_system, log_text_input
from skills import omnisense_skill
from skills import rpg_skill
from games.micro_rpg import MicroRPG
from ui.game_window import GameWindow
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

# ── RPG game state (shared between handle_input and rpg_game_loop) ──────────
_rpg_game = None
_rpg_running = False
_rpg_lock = threading.Lock()


def rpg_game_loop(iris, avatar, game_window):
    """Autonomous game loop — runs on a daemon thread."""
    global _rpg_game, _rpg_running

    log_system("[RPG] Game loop started.")
    game_window.show()

    with _rpg_lock:
        _rpg_game = MicroRPG()

    while True:
        with _rpg_lock:
            if not _rpg_running:
                break
            game = _rpg_game
            if game is None:
                break

        state = game.get_state()
        game_window.refresh(state)

        if game.is_over():
            # Final reaction from Iris
            prompt = rpg_skill.format_prompt(state)
            iris.chat(prompt, save=False, use_tools=False, avatar=avatar)
            break

        # Format prompt and get Iris's decision
        prompt = rpg_skill.format_prompt(state)
        game_window.set_status("Iris is thinking...")
        response = iris.chat(prompt, save=True, use_tools=False, avatar=avatar)

        # Parse action from Iris's response
        action = rpg_skill.parse_action(response or "")
        log_system(f"[RPG] Iris chose: {action}")
        game_window.set_status(f"Iris chose: {action}")

        # Execute action and refresh UI
        result = game.take_action(action)
        game_window.refresh(result["state"])

        # Brief pause between turns so it feels natural
        time.sleep(3)

    log_system("[RPG] Game loop ended.")
    game_window.set_status("Game over. Say 'start game' to play again.")
    iris.switch_mode("default", avatar=avatar)

    with _rpg_lock:
        _rpg_running = False
        _rpg_game = None


def handle_game_action(action, iris, avatar, game_window):
    global _rpg_running, _rpg_game

    if action == "stop_game":
        with _rpg_lock:
            if _rpg_running:
                _rpg_running = False
                _rpg_game = None
        iris.switch_mode("default", avatar=avatar)
        if game_window:
            game_window.hide()
        iris.vm.speak("Closing the game. Back to normal mode.", avatar=avatar)

    elif action == "start_micro_rpg":
        with _rpg_lock:
            if _rpg_running:
                iris.vm.speak(
                    "A game is already running. Stop it first.", avatar=avatar
                )
                return
            _rpg_running = True
        iris.switch_mode("gaming", avatar=avatar)
        threading.Thread(
            target=rpg_game_loop, args=(iris, avatar, game_window), daemon=True
        ).start()


def handle_input(text, iris, avatar, game_window=None):
    global _rpg_running, _rpg_game

    iris.reset_idle_timer()
    if iris.vm:
        try:
            iris.vm.interrupt_playback(avatar=avatar, reason="new_input")
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


def voice_loop(vm, iris, avatar, game_window):
    """Voice capture + Iris response loop — runs on a daemon thread."""
    print("\n--- Project Iris Wake-up Loop ---")
    print("Press Ctrl+C to terminate.")

    try:
        while True:
            text = vm.listen(avatar)
            if not text:
                continue
            handle_input(text, iris, avatar, game_window)

    except KeyboardInterrupt:
        print("\nExiting voice loop.")
        sys.exit(0)


def main():
    print("Starting Project Iris voice integration...")

    # --- Avatar window (must run on main thread) ---
    avatar = AvatarWindow()

    # --- Game window (hidden until game starts) ---
    game_window = GameWindow(avatar.window)

    # --- Voice and agent setup ---
    print("Loading local Whisper model...")
    vm = VoiceManager()
    iris = Iris()
    iris.vm = vm

    # --- Text input callback ---
    def on_text_submitted(text):
        log_text_input(text)
        threading.Thread(
            target=handle_input, args=(text, iris, avatar, game_window), daemon=True
        ).start()

    avatar.on_text_input = on_text_submitted

    # --- Mode switch callback ---
    avatar.on_mode_switch = lambda mode: iris.switch_mode(mode, avatar=avatar)

    # --- Game action callback ---
    avatar.on_game_action = lambda action: handle_game_action(
        action, iris, avatar, game_window
    )

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
        target=voice_loop, args=(vm, iris, avatar, game_window), daemon=True
    )
    voice_thread.start()

    # --- Block main thread on tkinter event loop ---
    avatar.run()


if __name__ == "__main__":
    main()
