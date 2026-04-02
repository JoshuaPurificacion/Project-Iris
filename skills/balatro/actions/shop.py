import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm
from skills.balatro.session import (
    _get_shop_card_by_index,
    _summarize_shop_item,
    _format_shop_item_summary,
    _early_game_buy_block_reason,
    _get_state_cards,
    EARLY_GAME_INTEREST_RESERVE,
    _build_run_profile,
    format_cost_for_display,
)


class BuyShopAction(BaseAction):
    """Execute buy_shop action."""

    @property
    def name(self) -> str:
        return "buy_shop"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["shop"]):
            error = f"buy_shop requested outside shop state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Argument validation
        args = self._get_args(ctx)
        if not args:
            error = "buy_shop requested without a shop index."
            self._log_error(ctx, error)
            # Force next round to prevent soft-lock
            ctx.controller.client.proceed_next()
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        card_idx = args[0]
        raw_state = ctx.tick_context.raw_state

        # Get current shop size for bounds checking
        shop_cards = _get_state_cards(raw_state, "shop")
        shop_size = len(shop_cards)

        # Convert from 1-based (LLM) to 0-based (API) with bounds check
        target_index = card_idx - 1
        if not (0 <= target_index < shop_size):
            if shop_size == 0:
                # Shop is empty — force proceed_next to escape or the loop will spin forever
                log_system(
                    "[Balatro] buy_shop: shop is empty, forcing proceed_next to escape."
                )
                ctx.controller.client.proceed_next()
                error = "buy_shop failed: shop is empty. Forced proceed_next to escape."
            else:
                error = f"buy_shop index {card_idx} is out of bounds. Shop has {shop_size} items (1-{shop_size})."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Check shop card exists
        shop_card = shop_cards[target_index]
        if shop_card is None:
            error = f"buy_shop index {card_idx} is out of bounds for the current shop."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Early game buy block check
        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        shop_summary = _summarize_shop_item(shop_card, raw_state, deck_name)
        reserve_block_reason = _early_game_buy_block_reason(
            raw_state, shop_card, deck_name
        )
        if reserve_block_reason:
            error = reserve_block_reason
            persona_reasoning = (
                f"Tried to buy {shop_summary['label']} for "
                f"${format_cost_for_display(shop_summary['cost'])}, but I need to keep "
                f"${EARLY_GAME_INTEREST_RESERVE} banked after the survival buy."
            )
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False, error=error, persona_reasoning=persona_reasoning
            )

        # Execute buy using 0-based index
        log_system(
            "[Balatro] Buying shop item "
            f"#{card_idx}: {_format_shop_item_summary(shop_summary)}."
        )
        result = ctx.controller.client.buy(card=target_index)

        if not result:
            api_error = str(getattr(ctx.controller.client, "_last_error", "") or "")
            if "joker slots are full" in api_error.lower() and BalatroAlgorithm.is_probable_joker(shop_card):
                replace_hint = str(shop_summary.get("replace_idx") or "").strip()
                if replace_hint:
                    error = (
                        f"Joker slots are full. Sell joker #{replace_hint} first, then buy {shop_summary['label']}."
                    )
                    persona_reasoning = (
                        f"Shop is full on jokers, so I should sell joker #{replace_hint} before buying {shop_summary['label']}."
                    )
                else:
                    error = "Joker slots are full. Sell a low-impact joker first before buying."
                    persona_reasoning = (
                        f"Could not buy {shop_summary['label']} because joker slots are full."
                    )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False, error=error, persona_reasoning=persona_reasoning
                )

            error = "Insufficient funds or invalid item."
            persona_reasoning = (
                f"Tried to buy {shop_summary['label']} for "
                f"${format_cost_for_display(shop_summary['cost'])}, but the shop rejected it."
            )
            log_system(
                "[Balatro] Buy failed (insufficient funds or invalid item). Forcing next round to prevent soft-lock."
            )
            self._log_error(ctx, error)
            ctx.controller.client.proceed_next()
            return ActionResult(
                succeeded=False, error=error, persona_reasoning=persona_reasoning
            )
        else:
            # Fix: Force state refresh so shop inventory updates instantly
            ctx.controller.refresh_state()
            ctx.tick_context.update_raw_state(ctx.controller.raw_state)

            persona_reasoning = (
                f"Bought {shop_summary['label']} for ${format_cost_for_display(shop_summary['cost'])}. "
                f"{ctx.planner_output.get('reasoning', '')}"
            ).strip()
            self._log_success(ctx, f"bought shop item #{card_idx}")
            return ActionResult(
                succeeded=True, error=None, persona_reasoning=persona_reasoning
            )


class BuyPackAction(BaseAction):
    """Execute buy_pack action."""

    @property
    def name(self) -> str:
        return "buy_pack"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["shop"]):
            error = f"buy_pack requested outside shop state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Argument validation
        args = self._get_args(ctx)
        if not args:
            error = "buy_pack requested without a pack index."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        pack_idx = args[0]
        raw_state = ctx.tick_context.raw_state

        # Get pack card
        pack_card = None
        pack_cards = _get_state_cards(raw_state, "packs")
        zero_idx = pack_idx - 1
        if 0 <= zero_idx < len(pack_cards):
            pack_card = pack_cards[zero_idx]

        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        pack_summary = _summarize_shop_item(pack_card, raw_state, deck_name)
        log_system(
            "[Balatro] Buying pack "
            f"#{pack_idx}: {_format_shop_item_summary(pack_summary)}."
        )

        # Execute buy — pack API is 0-based, convert from 1-based planner index
        result = ctx.controller.client.buy(pack=pack_idx - 1)

        if not result:
            error = "Insufficient funds or invalid pack."
            persona_reasoning = (
                f"Tried to buy {pack_summary['label']} for "
                f"${format_cost_for_display(pack_summary['cost'])}, but the pack purchase failed."
            )
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False, error=error, persona_reasoning=persona_reasoning
            )

        persona_reasoning = (
            f"Bought {pack_summary['label']} for ${format_cost_for_display(pack_summary['cost'])}. "
            f"{ctx.planner_output.get('reasoning', '')}"
        ).strip()
        self._log_success(ctx, f"bought pack #{pack_idx}")
        return ActionResult(
            succeeded=True, error=None, persona_reasoning=persona_reasoning
        )


class BuyVoucherAction(BaseAction):
    """Execute buy_voucher action."""

    @property
    def name(self) -> str:
        return "buy_voucher"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["shop"]):
            error = f"buy_voucher requested outside shop state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        raw_state = ctx.tick_context.raw_state

        # Get voucher card
        voucher_card = None
        voucher_cards = _get_state_cards(raw_state, "vouchers")
        if voucher_cards:
            voucher_card = voucher_cards[0]

        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        voucher_summary = _summarize_shop_item(voucher_card, raw_state, deck_name)
        log_system(
            f"[Balatro] Buying voucher: {_format_shop_item_summary(voucher_summary)}."
        )

        # Execute buy (voucher index is always 1)
        result = ctx.controller.client.buy(voucher=1)

        if not result:
            error = "Insufficient funds or invalid voucher."
            persona_reasoning = (
                f"Tried to buy {voucher_summary['label']} for "
                f"${format_cost_for_display(voucher_summary['cost'])}, but the voucher purchase failed."
            )
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False, error=error, persona_reasoning=persona_reasoning
            )

        persona_reasoning = (
            f"Bought {voucher_summary['label']} for ${format_cost_for_display(voucher_summary['cost'])}. "
            f"{ctx.planner_output.get('reasoning', '')}"
        ).strip()
        self._log_success(ctx, "bought voucher")
        return ActionResult(
            succeeded=True, error=None, persona_reasoning=persona_reasoning
        )


class StartRunAction(BaseAction):
    """Execute start_run action."""

    @property
    def name(self) -> str:
        return "start_run"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["menu"]):
            error = f"start_run requested outside menu state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Consistency mode: force Checkered deck for flush-focused runs.
        chosen_deck = "CHECKERED"

        log_system(f"[Balatro] Starting run with deck: {chosen_deck}")
        start_result = ctx.controller.client._call(
            "start", {"deck": chosen_deck, "stake": "WHITE"}
        )

        if start_result is not None:
            self._log_success(ctx, f"started run with deck {chosen_deck}")
        else:
            error = f"Failed to start run with deck '{chosen_deck}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Build run profile and patch system prompt on success
        ctx.session.run_profile = _build_run_profile(chosen_deck)
        ctx.session._patch_system_prompt_with_profile()

        return ActionResult(
            succeeded=True,
            error=None,
            persona_reasoning=ctx.planner_output.get("reasoning", ""),
        )

class RerollAction(BaseAction):
    """Execute reroll action."""

    @property
    def name(self) -> str:
        return "reroll"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["shop"]):
            error = f"reroll requested outside shop state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        raw_state = ctx.tick_context.raw_state
        reroll_cost = raw_state.get("round", {}).get("reroll_cost", 0)
        money = raw_state.get("money", 0)
        ante = raw_state.get("ante_num", 1)

        try:
            money = int(money)
        except (TypeError, ValueError):
            money = 0
        try:
            reroll_cost = int(reroll_cost)
        except (TypeError, ValueError):
            reroll_cost = 0

        # Avoid rerolling into poverty, especially early where interest reserve matters.
        reserve_target = EARLY_GAME_INTEREST_RESERVE if int(ante or 1) <= 2 else 25
        if (money - reroll_cost) < 15:
            error = (
                f"Reroll blocked: keep at least $15 after reroll. "
                f"money=${money}, reroll_cost=${reroll_cost}."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning=error)

        # If a strong item is already visible, do not burn money rerolling away from it.
        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        shop_cards = _get_state_cards(raw_state, "shop")
        for card in shop_cards:
            summary = _summarize_shop_item(card, raw_state, deck_name)
            advisor = str(summary.get("advisor") or "")
            impact = float(summary.get("impact_pct") or 0.0)
            if advisor == "HIGH SYNERGY" or impact >= 15.0:
                error = (
                    "Reroll blocked: high-value shop item already present "
                    f"({_format_shop_item_summary(summary)}). Buy or continue instead."
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False, error=error, persona_reasoning=error
                )

        if (money - reroll_cost) < reserve_target and money >= reserve_target:
            error = (
                f"Reroll blocked: would drop below reserve ${reserve_target}. "
                f"money=${money}, reroll_cost=${reroll_cost}."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning=error)

        log_system(f"[Balatro] Rerolling shop for ${reroll_cost}.")
        result = ctx.controller.client.reroll()

        if not result:
            api_error = getattr(ctx.controller.client, "_last_error", "Unknown API error")
            error = (
                f"Insufficient funds or unable to reroll (cost: ${reroll_cost}). "
                f"API said: {api_error}"
            )
            persona_reasoning = f"Tried to reroll the shop for ${reroll_cost}, but it failed ({api_error})."
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False, error=error, persona_reasoning=persona_reasoning
            )

        # Force state refresh so shop inventory updates instantly
        ctx.controller.refresh_state()
        ctx.tick_context.update_raw_state(ctx.controller.raw_state)

        persona_reasoning = (
            f"Rerolled the shop for ${reroll_cost}. "
            f"{ctx.planner_output.get('reasoning', '')}"
        ).strip()
        self._log_success(ctx, "rerolled the shop")
        return ActionResult(
            succeeded=True, error=None, persona_reasoning=persona_reasoning
        )
