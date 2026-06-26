import os
import sys
import time
import threading

import wake_up
import core.logger
from skills.balatro_bot.modules import balatro_telemetry
from wake_up import handle_game_action, handle_input, voice_loop
from core.avatar import AvatarWindow
from ui.game_window import GameWindow
from core.voice import VoiceManager
from core.iris_agent import Iris

def auto_main():
    print("Starting AUTO-RUN Balatro session...")

    avatar = AvatarWindow()
    game_window = GameWindow(avatar.window)

    vm = VoiceManager()
    iris = Iris()
    iris.vm = vm

    avatar.on_mode_switch = lambda mode: iris.switch_mode(mode, avatar=avatar)
    avatar.on_game_action = lambda action: handle_game_action(action, iris, avatar, game_window)

    print("Pre-warming LLM into VRAM...")
    threading.Thread(target=lambda: iris.chat("System init.", save=False, use_tools=False, avatar=None), daemon=True).start()

    # --- HOOK: AUTO START ---
    def delayed_start():
        time.sleep(3)
        print("\n\n>>> AUTO-RUN: Triggering start_balatro <<<\n\n")
        handle_game_action("start_balatro", iris, avatar, game_window)

    threading.Thread(target=delayed_start, daemon=True).start()

    # --- HOOK: AUTO EXIT ON GAME OVER ---
    original_log_system = core.logger.log_system

    def hooked_log_system(msg):
        original_log_system(msg)
        if "Game Over detected" in msg:
            print("\n\n>>> AUTO-RUN DETECTED GAME OVER. CLEANING UP AND EXITING IN 5 SECONDS... <<<\n\n")
            def exit_sequence():
                time.sleep(5)
                try:
                    balatro_telemetry.flush_telemetry()
                except Exception:
                    pass
                os._exit(0)
            threading.Thread(target=exit_sequence, daemon=True).start()

    core.logger.log_system = hooked_log_system
    wake_up.log_system = hooked_log_system
    import skills.balatro.session
    skills.balatro.session.log_system = hooked_log_system

    blink_thread = threading.Thread(target=avatar.blink_loop, daemon=True)
    blink_thread.start()

    voice_thread = threading.Thread(target=voice_loop, args=(vm, iris, avatar, game_window), daemon=True)
    voice_thread.start()

    # Block on Tkinter mainloop
    avatar.run()

if __name__ == "__main__":
    try:
        auto_main()
    finally:
        try:
            balatro_telemetry.flush_telemetry()
        except Exception:
            pass
