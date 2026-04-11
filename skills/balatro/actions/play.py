import re
import time
import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system, logger
from skills.balatro.session import BalatroAlgorithm
from skills.balatro.utils import _get_state_cards, _format_card, _format_hand
from skills.balatro_client import GameController


def _log_score_anomaly_safe(
    raw_state: dict,
    estimated_score: int,
    actual_score: int,
    delta_pct: float,
    hand_indices: list[int],
    hand_name: str,
) -> None:
    """Best-effort telemetry hook for score model drift."""
    try:
        from skills.balatro_bot.modules.balatro_telemetry import log_score_anomaly

        log_score_anomaly(
            raw_state=raw_state,
            estimated_score=estimated_score,
            actual_score=actual_score,
            delta_pct=delta_pct,
            hand_indices=hand_indices,
            hand_name=hand_name,
        )
    except Exception:
        return


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

        # Deterministic joker ordering optimization is play-phase only.
        joker_cards = _get_state_cards(raw_state, "jokers")
        optimize = BalatroAlgorithm.auto_optimize_jokers(
            joker_cards,
            deck_state=raw_state,
            hand_cards=hand_cards,
        )
        reorder_indices = optimize.get("reorder_indices") or []
        if optimize.get("changed") and len(reorder_indices) == len(joker_cards):
            current_order = list(range(1, len(joker_cards) + 1))
            working_order = list(current_order)
            for target_pos, desired_orig_idx in enumerate(reorder_indices):
                current_pos = working_order.index(desired_orig_idx)
                if current_pos == target_pos:
                    continue
                moved = ctx.controller.client.rearrange(
                    current_pos, target_pos, "jokers"
                )
                if not moved:
                    break
                # Avoid animation/network desync by waiting for state reconciliation
                # after each rearrange before submitting the next move.
                time.sleep(0.12)
                ctx.controller.refresh_state()
                ctx.tick_context.update_raw_state(ctx.controller.raw_state)
                card = working_order.pop(current_pos)
                working_order.insert(target_pos, card)
                # Keep local/raw state consistent for this tick.
                if 0 <= current_pos < len(joker_cards):
                    moved_card = joker_cards.pop(current_pos)
                    joker_cards.insert(target_pos, moved_card)
            # Refresh after deterministic reorder attempts.
            ctx.controller.refresh_state()
            ctx.tick_context.update_raw_state(ctx.controller.raw_state)
            raw_state = ctx.tick_context.raw_state
            hand_cards = _get_state_cards(raw_state, "hand")

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

        logger.debug(f"[Balatro] Hand: {_format_hand(hand_cards)}")
        played_cards = ", ".join(
            _format_card(hand_cards[i - 1]) for i in args if 1 <= i <= len(hand_cards)
        )
        logger.debug(f"[Balatro] Playing: {played_cards} (indices: {args})")

        # Execute play with retry logic
        return self._execute_play_with_retry(ctx, hand_cards, args)

    def _execute_play_with_retry(
        self,
        ctx: ActionContext,
        hand_cards: list,
        card_indices_to_play: list[int],
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
            played_attempt = ", ".join(
                _format_card(hand_cards[i - 1])
                for i in filtered_indices
                if 1 <= i <= len(hand_cards)
            )
            logger.debug(f"[Balatro] Play Attempt {attempt + 1}/{max_retries}...")

            pre_play_chips = 0
            try:
                pre_play_chips = int(
                    (ctx.tick_context.raw_state.get("round") or {}).get("chips", 0) or 0
                )
            except (TypeError, ValueError):
                pre_play_chips = 0

            success, err = ctx.controller.client.play_hand(zero_based_indices)
            if success:
                try:
                    # Refresh to capture post-play score reality before anomaly check.
                    ctx.controller.refresh_state()
                    ctx.tick_context.update_raw_state(ctx.controller.raw_state)

                    selected_cards = [
                        hand_cards[i - 1]
                        for i in filtered_indices
                        if 1 <= i <= len(hand_cards)
                    ]
                    components = BalatroAlgorithm._evaluate_hand_components(
                        selected_cards
                    )
                    hand_name = str(components.get("hand_name", ""))
                    estimated = BalatroAlgorithm.estimate_hand_score_for_indices(
                        hand_cards=hand_cards,
                        indices_1based=filtered_indices,
                        current_jokers=_get_state_cards(
                            ctx.tick_context.raw_state, "jokers"
                        ),
                        deck_name=(ctx.session.run_profile or {}).get("deck", ""),
                    )
                    post_play_chips = int(
                        (ctx.tick_context.raw_state.get("round") or {}).get("chips", 0)
                        or 0
                    )
                    actual = max(0, post_play_chips - pre_play_chips)
                    if actual > 0:
                        jokers_now = _get_state_cards(
                            ctx.tick_context.raw_state, "jokers"
                        )
                        has_rng = BalatroAlgorithm.lineup_has_probabilistic_jokers(
                            jokers_now
                        )
                        anomaly_threshold_pct = 40.0 if has_rng else 10.0
                        delta_pct = (
                            abs(actual - estimated) / max(1.0, float(actual)) * 100.0
                        )
                        if delta_pct > anomaly_threshold_pct:
                            _log_score_anomaly_safe(
                                raw_state=ctx.tick_context.raw_state,
                                estimated_score=estimated,
                                actual_score=actual,
                                delta_pct=delta_pct,
                                hand_indices=list(filtered_indices),
                                hand_name=hand_name,
                            )
                except Exception:
                    pass
                log_system(f"[Balatro] Played: {played_attempt}")
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

        logger.debug(f"[Balatro] Hand: {_format_hand(hand_cards)}")

        # Get planner indices or fall back to algorithm discard
        args = self._get_args(ctx)
        if not args:
            # Need card_indices_to_play for algorithm - get from best hand
            best_play_data = BalatroAlgorithm.find_best_hand(hand_cards)
            options = best_play_data.get("options", [])
            card_indices_to_play = options[0].get("indices", []) if options else []
            # Derive flush commitment from the top hand option.
            # "Flush" in best_hand_name catches Flush, Straight Flush, Flush Five, etc.
            _fallback_hand_name = options[0].get("hand_name", "") if options else ""
            _discard_target_type = (
                _fallback_hand_name if "Flush" in _fallback_hand_name else None
            )
            args = (
                BalatroAlgorithm.find_best_discard(
                    hand_cards,
                    card_indices_to_play,
                    target_hand_type=_discard_target_type,
                )
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
        logger.debug(f"[Balatro] Discarding: {discarded_cards} (indices: {args})")

        result = ctx.controller.client._call("discard", {"cards": zero_based_discards})
        if result is None:
            error = "Discard failed. Out of discards?"
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        log_system(f"[Balatro] Discarded: {discarded_cards}")
        return ActionResult(
            succeeded=True,
            error=None,
            persona_reasoning=ctx.planner_output.get("reasoning", ""),
        )
