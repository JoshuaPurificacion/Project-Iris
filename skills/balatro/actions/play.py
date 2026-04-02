import re
import time
import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system
from skills.balatro.session import BalatroAlgorithm, _get_state_cards
from skills.balatro_client import GameController


def _format_card(card: dict) -> str:
    """Format a single card as rank+suit (e.g., A♠, 9♦)."""
    if not isinstance(card, dict):
        return "?"
    value = card.get("value") or {}
    rank = value.get("rank", "")
    suit = value.get("suit", "")
    if rank and suit:
        suit_symbol = {
            "Spades": "♠",
            "S": "♠",
            "Hearts": "♥",
            "H": "♥",
            "Diamonds": "♦",
            "D": "♦",
            "Clubs": "♣",
            "C": "♣",
        }.get(suit, suit)
        return f"{rank}{suit_symbol}"
    return card.get("label", "?")


def _format_hand(hand_cards: list[dict]) -> str:
    """Format hand cards as [1] rank+suit, [2] rank+suit, ..."""
    if not hand_cards:
        return "empty"
    return ", ".join(
        f"[{i}]{_format_card(c)}" for i, c in enumerate(hand_cards, start=1)
    )


class PlayHandAction(BaseAction):
    """Execute play_hand action."""

    @property
    def name(self) -> str:
        return "play_hand"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["play", "hand"]):
            error = f"play_hand requested outside play/hand state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Get hand cards
        raw_state = ctx.tick_context.raw_state
        hand_cards = _get_state_cards(raw_state, "hand")
        if not hand_cards:
            error = "play_hand requested with no hand cards available."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Get planner indices or fall back to algorithm best hand
        args = self._get_args(ctx)
        if not args:
            # Get best hand from algorithm as fallback
            best_play_data = BalatroAlgorithm.find_best_hand(hand_cards)
            options = best_play_data.get("options", [])
            if not options:
                error = "play_hand requested without usable indices."
                self._log_error(ctx, error)
                return ActionResult(succeeded=False, error=error, persona_reasoning="")
            args = options[0].get("indices", [])

        log_system(f"[Balatro] Hand: {_format_hand(hand_cards)}")
        played_cards = ", ".join(
            _format_card(hand_cards[i - 1]) for i in args if 1 <= i <= len(hand_cards)
        )
        log_system(f"[Balatro] Playing: {played_cards} (indices: {args})")

        # Execute play with retry logic
        return self._execute_play_with_retry(ctx, hand_cards, args)

    def _execute_play_with_retry(
        self, ctx: ActionContext, hand_cards: list, card_indices_to_play: list[int]
    ) -> ActionResult:
        """Execute play_hand with retry logic for invalid indices."""
        attempt = 0
        max_retries = 3
        blacklist: set[int] = set()
        base_hand = list(enumerate(hand_cards, start=1))
        last_play_error = "play_hand failed before any cards were submitted."

        while attempt < max_retries:
            current_hand_size = len(base_hand)
            reported_count = ctx.tick_context.raw_state.get("hand", {}).get(
                "count", current_hand_size
            )
            if reported_count < current_hand_size:
                current_hand_size = reported_count
                base_hand = base_hand[:current_hand_size]

            filtered_indices = [
                idx
                for idx in card_indices_to_play
                if 1 <= idx <= current_hand_size and idx not in blacklist
            ]

            if not filtered_indices:
                last_play_error = f"No playable indices remained after blacklist on attempt {attempt}."
                break

            zero_based_indices = [idx - 1 for idx in filtered_indices]
            log_system(f"[Balatro] Play Attempt {attempt + 1}/{max_retries}...")

            success, err = ctx.controller.client.play_hand(zero_based_indices)
            if success:
                self._log_success(ctx, f"played indices {filtered_indices}")
                return ActionResult(
                    succeeded=True,
                    error=None,
                    persona_reasoning=ctx.planner_output.get("reasoning", ""),
                )

            log_system(f"[Balatro] Play failed: {err}")
            last_play_error = err or "play_hand failed."
            attempt += 1

            if not err:
                continue

            # Check for INVALID_STATE error - game state changed between planning and execution
            if "INVALID_STATE" in err or "requires one of these states" in err:
                log_system(
                    "[Balatro] State changed during planning, waiting for animations to complete..."
                )
                time.sleep(2.5)
                # Refresh state AND propagate to local context
                ctx.controller.refresh_state()
                ctx.tick_context.update_raw_state(ctx.controller.raw_state)
                continue

            # Explicitly handle Timeouts to prevent false blacklisting
            if "Timeout" in err:
                log_system(
                    "[Balatro] Socket timed out waiting for scoring animation. Re-syncing state..."
                )
                time.sleep(3.0)
                ctx.controller.refresh_state()
                ctx.tick_context.update_raw_state(ctx.controller.raw_state)
                continue

            # Check for invalid index error
            invalid_match = re.search(r"Invalid card index: (\d+)", err)
            if not invalid_match:
                continue

            bad_idx = int(invalid_match.group(1)) + 1  # convert back to 1-based
            blacklist.add(bad_idx)
            log_system(
                f"[Balatro] Blacklisting bad index {bad_idx}. Recomputing best hand."
            )

            # Remove blacklisted cards and recompute best hand
            base_hand = [(i, c) for i, c in base_hand if i not in blacklist]
            remaining_cards = [c for _, c in base_hand]
            recomputed = BalatroAlgorithm.find_best_hand(remaining_cards)
            recomputed_options = recomputed.get("options", [])
            local_indices = (
                recomputed_options[0].get("indices", []) if recomputed_options else []
            )

            # Remap local indices back to absolute 1-based
            remapped = []
            for li in local_indices:
                if 1 <= li <= len(base_hand):
                    remapped.append(base_hand[li - 1][0])

            if remapped:
                card_indices_to_play = remapped
            else:
                last_play_error = (
                    "Recomputed best hand produced no valid indices after blacklist."
                )
                break

        # If we get here, all retries failed
        self._log_error(ctx, last_play_error)
        return ActionResult(
            succeeded=False, error=last_play_error, persona_reasoning=""
        )


class DiscardAction(BaseAction):
    """Execute discard action."""

    @property
    def name(self) -> str:
        return "discard"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["play", "hand"]):
            error = f"discard requested outside play/hand state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Get hand cards
        raw_state = ctx.tick_context.raw_state

        # Hard guardrail: check discards_left before anything else
        round_info = raw_state.get("round", {})
        discards_left = int(round_info.get("discards_left", 0))
        if discards_left <= 0:
            error = "SYSTEM ERROR: 0 discards left. You MUST use 'play_hand'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning=error)

        hand_cards = _get_state_cards(raw_state, "hand")
        if not hand_cards:
            error = "discard requested with no hand cards available."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        log_system(f"[Balatro] Hand: {_format_hand(hand_cards)}")

        # Get planner indices or fall back to algorithm discard
        args = self._get_args(ctx)
        if not args:
            # Need card_indices_to_play for algorithm - get from best hand
            best_play_data = BalatroAlgorithm.find_best_hand(hand_cards)
            options = best_play_data.get("options", [])
            card_indices_to_play = options[0].get("indices", []) if options else []
            args = (
                BalatroAlgorithm.find_best_discard(hand_cards, card_indices_to_play)
                or []
            )

        if not args:
            error = "discard requested without usable indices."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        discarded_cards = ", ".join(
            _format_card(hand_cards[i - 1]) for i in args if 1 <= i <= len(hand_cards)
        )
        zero_based_discards = [idx - 1 for idx in args]
        log_system(f"[Balatro] Discarding: {discarded_cards} (indices: {args})")

        result = ctx.controller.client._call("discard", {"cards": zero_based_discards})
        if result is None:
            error = "Discard failed. Out of discards?"
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Refresh state and show updated hand
        ctx.controller.refresh_state()
        new_hand = ctx.controller.raw_state.get("hand", {}).get("cards", [])
        if new_hand:
            formatted_hand = ", ".join(
                [f"[{i + 1}]{_format_card(c)}" for i, c in enumerate(new_hand)]
            )
            log_system(f"[Balatro] Hand after discard: {formatted_hand}")

        self._log_success(ctx, f"discarded indices {args}")
        return ActionResult(
            succeeded=True,
            error=None,
            persona_reasoning=ctx.planner_output.get("reasoning", ""),
        )
