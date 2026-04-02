import re
import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm


def _extract_enhancement_from_effect(effect: str) -> str | None:
    """Parse the target enhancement name from a consumable's effect description.

    Matches the pattern '... to <Name> Cards' at the end of the string, e.g.:
      'Enhances 2 selected cards to Lucky Cards'  -> 'LUCKY'
      'Enhances 1 selected card to Wild Cards'    -> 'WILD'

    Returns the enhancement name in uppercase, or None if no match.
    """
    match = re.search(r'to (\w+) Cards?', effect, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _card_modifier_enhancement(card: dict) -> str | None:
    """Safely read modifier.enhancement from a hand card dict.

    The modifier field is either a dict {'enhancement': 'LUCKY'} or an
    empty list [] for base cards.
    """
    modifier = card.get("modifier", [])
    if isinstance(modifier, dict):
        return (modifier.get("enhancement") or "").upper() or None
    return None


def _get_consumable_cards(raw_state: dict) -> list[dict]:
    """Return consumables from either known payload spelling."""
    for key in ("consumeables", "consumables"):
        container = raw_state.get(key, {})
        if isinstance(container, dict):
            cards = container.get("cards", [])
            if isinstance(cards, list):
                return cards
    return []


class SellJokerAction(BaseAction):
    """Execute sell_joker action."""

    @property
    def name(self) -> str:
        return "sell_joker"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # Argument validation
        args = self._get_args(ctx)
        if not args:
            error = "sell_joker requested without an index."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        try:
            idx = int(args[0]) - 1  # LLM is 1-based, Lua/API is 0-based
        except (ValueError, TypeError):
            error = "sell_joker index must be an integer."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Check bounds
        raw_state = ctx.tick_context.raw_state
        jokers = raw_state.get("jokers", {}).get("cards", [])
        if not (0 <= idx < len(jokers)):
            error = (
                f"sell_joker index {args[0]} is out of bounds for {len(jokers)} jokers."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        hand_cards = raw_state.get("hand", {}).get("cards", [])
        if not isinstance(hand_cards, list):
            hand_cards = []

        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        safe_sells = BalatroAlgorithm.identify_safe_sell_jokers(
            jokers,
            hand_cards,
            deck_name=deck_name,
            tolerance_pct=2.5,
        )
        safe_indices = {int(row.get("index", 0)) for row in safe_sells}
        selected_idx_1b = idx + 1

        if selected_idx_1b not in safe_indices:
            # Allow selling a protected joker only when a better replacement is visible in shop.
            allow_for_upgrade = False
            shop_cards = raw_state.get("shop", {}).get("cards", [])
            if isinstance(shop_cards, list):
                for card in shop_cards:
                    impact = BalatroAlgorithm.evaluate_shop_joker_impact(
                        card,
                        jokers,
                        hand_cards,
                        deck_name=deck_name,
                        deck_state=raw_state,
                    )
                    replace_idx = impact.get("replacement_index")
                    delta_pct = float(impact.get("delta_pct", 0.0))
                    if (
                        replace_idx == selected_idx_1b
                        and delta_pct >= 12.0
                        and BalatroAlgorithm.is_probable_joker(card)
                    ):
                        allow_for_upgrade = True
                        break

            if not allow_for_upgrade:
                name = jokers[idx].get("label") or jokers[idx].get("key") or f"Joker {selected_idx_1b}"
                error = (
                    f"sell_joker blocked: {name} is high-impact for current scoring. "
                    "Sell a low-impact joker instead."
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False,
                    error=error,
                    persona_reasoning="I should keep high-impact jokers and sell low-impact ones first.",
                )

        # Execute sell
        log_system(
            f"[Balatro] Selling joker at 0-based index {idx} (LLM said {args[0]})."
        )
        sold = ctx.controller.client.sell(idx, "jokers")

        if sold:
            self._log_success(ctx, f"sold joker at index {args[0]}")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "sell_joker API call failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


class SellConsumableAction(BaseAction):
    """Execute sell_consumable action."""

    @property
    def name(self) -> str:
        return "sell_consumable"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # Argument validation
        args = self._get_args(ctx)
        if not args:
            error = "sell_consumable requested without an index."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        try:
            idx = int(args[0]) - 1  # LLM is 1-based, Lua/API is 0-based
        except (ValueError, TypeError):
            error = "sell_consumable index must be an integer."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Check bounds
        raw_state = ctx.tick_context.raw_state
        consumables = _get_consumable_cards(raw_state)
        if not (0 <= idx < len(consumables)):
            error = f"sell_consumable index {args[0]} is out of bounds for {len(consumables)} consumables."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Execute sell
        log_system(
            f"[Balatro] Selling consumable at 0-based index {idx} (LLM said {args[0]})."
        )
        sold = ctx.controller.client.sell(idx, "consumeables")

        if sold:
            self._log_success(ctx, f"sold consumable at index {args[0]}")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "sell_consumable API call failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


class UseConsumableAction(BaseAction):
    """Execute use_consumable action."""

    @property
    def name(self) -> str:
        return "use_consumable"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # Argument validation
        args = self._get_args(ctx)
        if not args:
            error = "use_consumable requested without an index."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        try:
            idx = int(args[0]) - 1
            # Extract any subsequent targets and convert to 0-based
            targets = [int(x) - 1 for x in args[1:]] if len(args) > 1 else None
        except (ValueError, TypeError):
            error = "use_consumable indices must be integers."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Check bounds
        raw_state = ctx.tick_context.raw_state
        consumables = _get_consumable_cards(raw_state)
        if not (0 <= idx < len(consumables)):
            error = f"use_consumable index {args[0]} is out of bounds for {len(consumables)} consumables."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        consumable = consumables[idx]
        current_state = (raw_state.get("state", "") or "").lower()

        # Prevent invalid API calls in pack/booster flows where consumable use is disallowed.
        if "pack" in current_state or "booster" in current_state:
            consumable_label = consumable.get("label", f"consumable {args[0]}")
            error = (
                f"Cannot use {consumable_label} during pack/booster selection. "
                "Finish pack choices first."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # In shop, targeted consumables are invalid because there is no hand-selection flow.
        effect_text = (consumable.get("value") or {}).get("effect", "")
        requires_targets = "selected" in effect_text.lower()
        if "shop" in current_state and (targets or requires_targets):
            consumable_label = consumable.get("label", f"consumable {args[0]}")
            error = (
                f"Cannot use targeted consumable {consumable_label} in shop. "
                "Use it during hand-play states where card targets can be selected."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Tier 1: Dynamic guardrail — block consumables that require jokers when none exist
        card_effect = (consumable.get("value") or {}).get("effect", "").lower()
        jokers = raw_state.get("jokers", {}).get("cards", [])
        if "joker" in card_effect and len(jokers) == 0:
            if "create" not in card_effect and "spawn" not in card_effect:
                error = "Cannot use: This consumable requires at least one active Joker. Buy a Joker first."
                self._log_error(ctx, error)
                return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Tier 2: Block wasted enhancement — target card already has this enhancement
        consumable_modifier = consumable.get("modifier") or {}
        is_enhance_tarot = (
            isinstance(consumable_modifier, dict)
            and consumable_modifier.get("enhancement", "").upper() == "ENHANCE"
        )
        if is_enhance_tarot and targets:
            effect_str = (consumable.get("value") or {}).get("effect", "")
            applied_enh = _extract_enhancement_from_effect(effect_str)
            if applied_enh:
                hand_cards = raw_state.get("hand", {}).get("cards", [])
                already_enhanced = []
                for t in targets:
                    if 0 <= t < len(hand_cards):
                        existing = _card_modifier_enhancement(hand_cards[t])
                        if existing == applied_enh:
                            already_enhanced.append(
                                hand_cards[t].get("label", f"card {t + 1}")
                            )
                if already_enhanced:
                    names = ", ".join(already_enhanced)
                    error = (
                        f"Wasted use blocked: {names} "
                        f"{'is' if len(already_enhanced) == 1 else 'are'} already "
                        f"'{applied_enh}' enhanced. Choose cards without that enhancement."
                    )
                    self._log_error(ctx, error)
                    return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Execute use
        log_system(
            f"[Balatro] Using consumable at 0-based index {idx} with targets {targets}."
        )
        used = ctx.controller.client.use(idx, targets=targets)

        if used:
            # Refresh state immediately so tick_context reflects the removed consumable.
            # Without this, any subsequent read of consumeables in the same tick would
            # see the old list with shifted indices (e.g. what was index 1 appears as
            # index 0 once index 0 is consumed). Mirrors the pattern in DiscardAction.
            ctx.controller.refresh_state()
            ctx.tick_context.update_raw_state(ctx.controller.raw_state)

            remaining = ctx.controller.raw_state.get("consumeables", {}).get("cards", [])
            log_system(
                f"[Balatro] Consumables remaining after use: {len(remaining)}"
                + (f" — {[c.get('label', '?') for c in remaining]}" if remaining else "")
            )

            self._log_success(ctx, f"used consumable at index {args[0]}")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "use_consumable API call failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


class RearrangeJokerAction(BaseAction):
    """Execute rearrange_joker action."""

    @property
    def name(self) -> str:
        return "rearrange_joker"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # Argument validation
        args = self._get_args(ctx)
        if len(args) < 2:
            error = "rearrange_joker requires two indices."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        try:
            from_idx = int(args[0]) - 1
            to_idx = int(args[1]) - 1
        except (ValueError, TypeError):
            error = "rearrange_joker indices must be integers."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Check bounds
        raw_state = ctx.tick_context.raw_state
        jokers = raw_state.get("jokers", {}).get("cards", [])
        if not (0 <= from_idx < len(jokers)) or not (0 <= to_idx < len(jokers)):
            error = f"rearrange_joker indices {args[0]}, {args[1]} are out of bounds for {len(jokers)} jokers."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # No-op check
        if from_idx == to_idx:
            error = "rearrange_joker source and destination were the same."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Execute rearrange
        log_system(
            f"[Balatro] Moving Joker from {from_idx} to {to_idx} (LLM said {args[0]}->{args[1]})."
        )
        moved = ctx.controller.client.rearrange(from_idx, to_idx, "jokers")

        if moved:
            self._log_success(ctx, f"rearranged joker {args[0]} -> {args[1]}")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "rearrange_joker API call failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


class RearrangeConsumableAction(BaseAction):
    """Execute rearrange_consumable action."""

    @property
    def name(self) -> str:
        return "rearrange_consumable"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # Argument validation
        args = self._get_args(ctx)
        if len(args) < 2:
            error = "rearrange_consumable requires two indices."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        try:
            from_idx = int(args[0]) - 1
            to_idx = int(args[1]) - 1
        except (ValueError, TypeError):
            error = "rearrange_consumable indices must be integers."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Check bounds
        raw_state = ctx.tick_context.raw_state
        consumables = _get_consumable_cards(raw_state)
        if not (0 <= from_idx < len(consumables)) or not (
            0 <= to_idx < len(consumables)
        ):
            error = f"rearrange_consumable indices {args[0]}, {args[1]} are out of bounds for {len(consumables)} consumables."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # No-op check
        if from_idx == to_idx:
            error = "rearrange_consumable source and destination were the same."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Execute rearrange
        log_system(f"[Balatro] Moving consumable from {from_idx} to {to_idx}.")
        moved = ctx.controller.client.rearrange(from_idx, to_idx, "consumeables")

        if moved:
            self._log_success(ctx, f"rearranged consumable {args[0]} -> {args[1]}")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "rearrange_consumable API call failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")
