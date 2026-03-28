import threading
import time
import re
from core.logger import log_system
from skills.balatro_client import GameController
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm

_game_controller = None
_balatro_running = False


def start_balatro(iris, avatar):
    global _game_controller, _balatro_running
    iris.switch_mode("balatro", avatar=avatar)
    if not _balatro_running:
        _game_controller = GameController()
        _balatro_running = True
        threading.Thread(target=game_loop, args=(iris, avatar), daemon=True).start()


def stop_balatro():
    global _balatro_running
    _balatro_running = False


def game_loop(iris, avatar):
    global _game_controller, _balatro_running
    log_system("[Balatro] Game loop started.")

    last_state_snapshot = None

    while _balatro_running:
        success = _game_controller.refresh_state()
        if not success:
            time.sleep(2)
            continue

        raw_state = _game_controller.raw_state

        # Optional: only chat when state changes to reduce load
        state_snapshot = raw_state.get("state", "") + str(
            raw_state.get("round", {}).get("hands_left", "")
        )
        if state_snapshot == last_state_snapshot:
            time.sleep(5)
            continue
        last_state_snapshot = state_snapshot

        best_hand_name = "None"
        card_indices_to_play = []
        hand_cards = []

        if "cards" in raw_state.get("hand", {}):
            hand_cards = raw_state.get("hand", {}).get("cards", [])
            if hand_cards:
                best_play = BalatroAlgorithm.find_best_hand(hand_cards)
                best_hand_name = best_play.get("hand_name", "None")
                card_indices_to_play = best_play.get("card_indices", [])

        state_json = _game_controller.get_context_for_llm()
        prompt = (
            f"Recommended play: {best_hand_name}. "
            f"What is the vibe, and what is your next move?"
        )

        response = iris.chat(
            prompt,
            avatar=avatar,
            ephemeral_context=f"Here is the current game state:\n{state_json}",
        )

        while (
            not iris.vm.speech_queue.empty() or iris.vm.is_speaking.is_set()
        ) and _balatro_running:
            time.sleep(0.5)

        if not _balatro_running:
            break

        match = re.search(r"\[(\w+)(?:\s+(\d+))?\]", response or "")
        if match:
            action_tag = match.group(1).lower()
            argument = match.group(2)
            log_system(f"[Balatro] Executing action: {action_tag} with arg: {argument}")

            if action_tag == "play_hand" and card_indices_to_play:
                attempt = 0
                max_retries = 3
                blacklisted_indices: list[int] = []

                # We'll retry up to max_retries if Lua rejects a card index.
                while attempt < max_retries:
                    # Refresh state to avoid stale indices
                    if _game_controller.refresh_state():
                        hand_state = _game_controller.raw_state.get("hand", {})
                        hand_cards = hand_state.get("cards", [])

                    # Filter out blacklisted indices from the hand and recompute best play
                    if blacklisted_indices:
                        filtered_hand = [
                            c
                            for i, c in enumerate(hand_cards)
                            if (i + 1) not in blacklisted_indices
                        ]
                    else:
                        filtered_hand = hand_cards

                    if filtered_hand:
                        fresh_play = BalatroAlgorithm.find_best_hand(filtered_hand)
                        local_indices = fresh_play.get("card_indices", [])

                        # Map local indices (relative to filtered_hand) back to original absolute indices (1-based)
                        remapped_indices = []
                        for local_idx in local_indices:
                            card = filtered_hand[local_idx - 1]
                            original_idx = hand_cards.index(card) + 1
                            remapped_indices.append(original_idx)
                        card_indices_to_play = remapped_indices

                    current_hand_size = len(hand_cards)
                    reported_count = _game_controller.raw_state.get("hand", {}).get(
                        "count", current_hand_size
                    )
                    if reported_count < current_hand_size:
                        log_system(
                            f"[Balatro] Hand count mismatch: cards len={current_hand_size}, reported count={reported_count}. Using reported count."
                        )
                        current_hand_size = reported_count

                    filtered_indices = [
                        idx
                        for idx in card_indices_to_play
                        if 1 <= idx <= current_hand_size
                    ]

                    if len(filtered_indices) != len(card_indices_to_play):
                        log_system(
                            f"[Balatro] ABORT: Filtered invalid indices {card_indices_to_play} down to {filtered_indices} with hand size {current_hand_size}."
                        )

                    if not filtered_indices:
                        break

                    success, err = _game_controller.client.play_hand(filtered_indices)
                    if success:
                        break

                    if err and "Invalid card index" in err:
                        match_err = re.search(r"index[:\s]+(\d+)", err)
                        if match_err:
                            bad_idx = int(match_err.group(1))
                            log_system(
                                f"[Balatro] Lua rejected index {bad_idx}. Blacklisting and retrying..."
                            )
                            blacklisted_indices.append(bad_idx)
                            attempt += 1
                            continue

                    # If other errors or retries exhausted, stop
                    log_system(f"[Balatro] Play failed: {err}")
                    break

            elif action_tag == "discard" and hand_cards:
                discard_indices = BalatroAlgorithm.find_best_discard(
                    hand_cards, card_indices_to_play
                )
                if discard_indices:
                    _game_controller.client._call("discard", {"cards": discard_indices})

            elif action_tag in ["skip_blind", "skip"]:
                _game_controller.client._call("skip")

            elif action_tag == "buy_shop" and argument:
                _game_controller.client._call("buy", {"index": int(argument)})

            elif action_tag == "continue":
                _game_controller.client.proceed_next()
        else:
            log_system("[Balatro] No action tag parsed from response.")

        time.sleep(5)
