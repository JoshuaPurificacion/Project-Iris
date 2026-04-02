"""
Integration smoke test for the live BalatroBot instance.

Usage:
    BALATRO_INTEGRATION=1 python test_balatro_integration.py
    BALATRO_LOOP=1 python test_balatro_integration.py --loop  # continuous polling (Ctrl+C to stop)

Environment variables:
  BALATRO_HOST (default: 127.0.0.1)
  BALATRO_PORT (default: 12346)

This runs against a real BalatroBot process. It will:
  1) Fetch current state.
  2) If in blind select, advance.
  3) If in shop, attempt to buy the first item then advance.
  4) If in play with cards, auto-select a best hand and play it.

Guarded by BALATRO_INTEGRATION so it does not run in CI by default.
"""

import argparse
import os
import sys
import time
import unittest

from skills.balatro_client import BalatroClient
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm


def _host_port():
    host = os.getenv("BALATRO_HOST", "127.0.0.1")
    port = int(os.getenv("BALATRO_PORT", "12346"))
    return host, port


def _has_cards(container: dict | None) -> bool:
    return bool(container and container.get("cards"))


def _play_best_hand_from_state(client: BalatroClient, state: dict) -> str:
    """Play the algorithm's best hand; return a short action summary."""
    hand = state.get("hand", {})
    cards = hand.get("cards", [])
    if not cards:
        return "no-hand"

    best_play = BalatroAlgorithm.find_best_hand(cards)
    options = best_play.get("options", [])
    indices = options[0].get("indices", []) if options else []
    if not indices:
        return "no-indices"

    zero_based = [i - 1 for i in indices]
    ok, err = client.play_hand(zero_based)
    if ok:
        return f"play_hand {indices}"
    return f"play_hand failed: {err}"


def _attempt_blind_selection(client: BalatroClient) -> str:
    """Use documented API: select() or skip() while in BLIND_SELECT."""
    if client.select():
        return "select"
    if client.skip():
        return "skip"
    last_err = getattr(client, "_last_error", "unknown error")
    return f"blind_select failed: {last_err}"


def _step_once(client: BalatroClient) -> list[str]:
    """Single integration step; returns a list of action summaries."""
    actions: list[str] = []
    state = client.get_game_state()
    if not state:
        return ["state fetch failed"]

    phase = (state.get("state") or "").lower()

    if "round_eval" in phase:
        # Cash out rewards and move to shop
        if client._call("cash_out") is not None:
            actions.append("cash_out")
        else:
            actions.append(
                f"cash_out failed: {getattr(client, '_last_error', 'unknown error')}"
            )
        time.sleep(0.2)
        state = client.get_game_state() or {}
        phase = (state.get("state") or "").lower()

    if "shop" in phase:
        shop = state.get("shop", {})
        if _has_cards(shop):
            bought = client.buy(card=1)
            if bought:
                actions.append("buy_shop 1")
            else:
                actions.append(
                    f"buy_shop failed: {getattr(client, '_last_error', 'unknown error')}"
                )
            time.sleep(0.2)
            state = client.get_game_state() or {}
            phase = (state.get("state") or "").lower()

        proceeded = client.proceed_next()
        actions.append("next_round" if proceeded else "next_round failed")
        time.sleep(0.2)
        state = client.get_game_state() or {}
        phase = (state.get("state") or "").lower()

    if "blind" in phase:
        actions.append(_attempt_blind_selection(client))
        time.sleep(0.2)
        state = client.get_game_state() or {}
        phase = (state.get("state") or "").lower()

    if _has_cards(state.get("hand")):
        actions.append(_play_best_hand_from_state(client, state))
    else:
        actions.append(f"no-hand ({phase})")

    return actions


def run_continuous_loop(
    poll_interval: float = 1.0, max_iters: int | None = None
) -> None:
    """Continuously polls the live bot, executing simple actions until stopped."""
    host, port = _host_port()
    client = BalatroClient(host=host, port=port, timeout=10.0)

    iteration = 0
    try:
        while True:
            actions = _step_once(client)
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp}] iter {iteration}: {'; '.join(actions)}")

            iteration += 1
            if max_iters is not None and iteration >= max_iters:
                break

            time.sleep(max(poll_interval, 0.1))
    except KeyboardInterrupt:
        print("Interrupted by user; exiting loop.")


@unittest.skipUnless(
    os.getenv("BALATRO_INTEGRATION") == "1", "Set BALATRO_INTEGRATION=1 to run"
)
class TestBalatroIntegration(unittest.TestCase):
    def setUp(self):
        host, port = _host_port()
        self.client = BalatroClient(host=host, port=port, timeout=10.0)

    def _play_best_hand(self, state: dict):
        hand = state.get("hand", {})
        cards = hand.get("cards", [])
        if not cards:
            self.skipTest("No cards available to play.")
        best_play = BalatroAlgorithm.find_best_hand(cards)
        options = best_play.get("options", [])
        indices = options[0].get("indices", []) if options else []
        if not indices:
            self.skipTest("Algorithm returned no playable indices.")
        zero_based = [i - 1 for i in indices]
        ok, err = self.client.play_hand(zero_based)
        self.assertTrue(ok, f"play_hand failed: {err}")

    def test_live_bot_smoke(self):
        state = self.client.get_game_state()
        self.assertIsNotNone(
            state, "Failed to fetch game state; ensure BalatroBot is running."
        )

        phase = (state.get("state") or "").lower()

        if "blind" in phase:
            result = _attempt_blind_selection(self.client)
            self.assertIn(result, {"select", "skip"}, result)
            time.sleep(0.2)
            state = self.client.get_game_state()

        phase = (state.get("state") or "").lower()

        if "shop" in phase:
            shop = state.get("shop", {})
            if _has_cards(shop):
                bought = self.client.buy(card=1)
                self.assertTrue(
                    bought,
                    f"buy_shop failed: {getattr(self.client, '_last_error', 'unknown error')}",
                )
                time.sleep(0.2)
                state = self.client.get_game_state()
            # Advance out of shop.
            proceeded = self.client.proceed_next()
            self.assertTrue(proceeded, "Proceed next (shop) failed.")
            time.sleep(0.2)
            state = self.client.get_game_state()

        # Final attempt: if we have a playable hand, play it.
        if _has_cards(state.get("hand")):
            self._play_best_hand(state)
        else:
            self.skipTest(
                "Reached a phase without a playable hand; rerun during play phase."
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Balatro integration smoke / loop runner"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuously poll the live bot (Ctrl+C to stop).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between polling iterations in loop mode (default: 1.0).",
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=None,
        help="Optional cap on iterations in loop mode.",
    )

    args, remaining = parser.parse_known_args()

    if args.loop or os.getenv("BALATRO_LOOP") == "1":
        run_continuous_loop(poll_interval=args.interval, max_iters=args.max_iters)
    else:
        unittest.main(argv=[sys.argv[0]] + remaining, verbosity=2)
