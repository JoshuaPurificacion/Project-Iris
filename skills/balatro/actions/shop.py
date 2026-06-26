import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm
from skills.balatro.shop_analysis import (
    _summarize_shop_item,
    _format_shop_item_summary,
    _early_game_buy_block_reason,
    _any_affordable,
    EARLY_GAME_INTEREST_RESERVE,
    EARLY_GAME_JOKER_TARGET,
    format_cost_for_display,
    _extract_cost_value,
    _extract_advisor_core,
    _count_survival_jokers,
    CRITICAL_SPEND_FLOOR,
    _get_reserve_for_ante,
)
from skills.balatro.utils import _get_state_cards
from skills.balatro.utils import _safe_int
from skills.balatro.session import _build_run_profile


# Survival-gate margin by ante. Relaxes the boss-blind threshold at high antes
# so late-game pivots aren't frozen by imprecise score estimates.
# Default (unknown ante) is 1.0 — no adjustment.
BLIND_SAFETY_MARGIN_BY_ANTE: dict[int, float] = {
    1: 1.5,
    2: 1.5,
    3: 1.3,
    4: 1.2,
    5: 1.0,
    6: 0.9,
    7: 0.85,
    8: 0.8,
}


def _get_tick_shop_cache(ctx: ActionContext, deck_name: str = "") -> typing.Any | None:
    """Return TickContext shop cache when available and usable."""
    ensure_shop_cache = getattr(ctx.tick_context, "ensure_shop_cache", None)
    if not callable(ensure_shop_cache):
        return None
    try:
        cache = ensure_shop_cache(deck_name)
        if cache is None:
            return None

        required_list_fields = (
            "shop_cards",
            "shop_summaries",
            "pack_cards",
            "pack_summaries",
            "voucher_cards",
            "voucher_summaries",
            "jokers",
        )
        if all(isinstance(getattr(cache, field, None), list) for field in required_list_fields):
            return cache
        return None
    except Exception:
        return None


def _shop_item_type(card: dict) -> str:
    """Classify a shop card into high-level buy-slot behavior."""
    if BalatroAlgorithm.is_probable_joker(card):
        return "joker"
    key = str(card.get("key") or "").lower()
    label = str(card.get("label") or "").lower()
    if key.startswith("v_"):
        return "voucher"
    if key.startswith("p_") or "pack" in label:
        return "pack"
    # Most Tarot/Planet/Spectral cards are consumables.
    if key.startswith("c_"):
        return "consumable"
    return "other"


def _get_consumable_slot_capacity(raw_state: dict) -> int:
    """Best-effort consumable slot capacity from state (default 2)."""
    for key in ("consumeables", "consumables"):
        container = raw_state.get(key)
        if isinstance(container, dict):
            size_val = container.get("size")
            try:
                if size_val is not None:
                    return max(1, int(size_val))
            except (TypeError, ValueError):
                pass
    return 2


def _build_dynamic_slot_full_error(
    shop_card: dict,
    raw_state: dict,
    replace_hint: str = "",
) -> str | None:
    """Return semantic preflight error for slot-limited buys, else None."""
    item_type = _shop_item_type(shop_card)
    if item_type == "joker":
        jokers = _get_state_cards(raw_state, "jokers")
        max_jokers = _safe_int((raw_state.get("jokers") or {}).get("size"), 5)
        if len(jokers) >= max(1, max_jokers):
            hand_cards = _get_state_cards(raw_state, "hand")
            safe_sells = BalatroAlgorithm.identify_safe_sell_jokers(
                current_jokers=jokers,
                hand_cards=hand_cards,
                tolerance_pct=5.0,
                deck_state=raw_state,
            )
            best_sell = safe_sells[0] if safe_sells else None
            sell_hint = ""
            if isinstance(best_sell, dict):
                sell_label = str(best_sell.get("label") or "").strip()
                sell_index = best_sell.get("index")
                loss_abs = best_sell.get(
                    "combined_loss_abs", best_sell.get("loss_abs", 0)
                )
                if sell_label and sell_index:
                    sell_hint = (
                        f" Math suggests selling '{sell_label}' (index {sell_index}) "
                        f"is lowest impact (~-{int(loss_abs)} pts expected drop)."
                    )
            if replace_hint:
                return (
                    f"Error: Joker slots full ({len(jokers)}/{max_jokers}). "
                    f"Use 'sell_joker' first (suggested index: {replace_hint})."
                    f"{sell_hint}"
                )
            return (
                f"Error: Joker slots full ({len(jokers)}/{max_jokers}). "
                f"You must use 'sell_joker' first.{sell_hint}"
            )
        return None

    if item_type == "consumable":
        consumables = _get_state_cards(raw_state, "consumeables")
        max_consumables = _get_consumable_slot_capacity(raw_state)
        if len(consumables) >= max_consumables:
            return (
                f"Error: Consumable slots full ({len(consumables)}/{max_consumables}). "
                "Use 'use_consumable' or 'sell_consumable' first."
            )
        return None

    # Vouchers/packs/other items are not blocked by joker/consumable slot fullness.
    return None


def _shop_boss_requirement_multiplier(raw_state: dict) -> float:
    """Conservative safety multiplier for boss-blind survival gating."""
    blinds = raw_state.get("blinds") or {}
    if not isinstance(blinds, dict):
        return 1.0
    boss = blinds.get("boss") or {}
    if not isinstance(boss, dict):
        return 1.0
    status = str(boss.get("status", "")).lower()
    if status not in ("active", "current", "playing"):
        return 1.0
    return 1.5


def _find_affordable_non_rerollable_option(
    raw_state: dict,
    money: int,
    deck_name: str = "",
    shop_cache: typing.Any | None = None,
) -> tuple[str, int, dict] | None:
    """Return best affordable non-rerollable option (voucher first, then pack)."""

    def _advisor_not_bad(summary: dict) -> bool:
        advisor = _extract_advisor_core(str(summary.get("advisor") or ""))
        return advisor in ("HIGH SYNERGY", "MODERATE VALUE")

    def _is_affordable(summary: dict, fallback_card: dict) -> bool:
        summary_cost = _extract_cost_value(summary.get("cost"))
        card_cost = _extract_cost_value((fallback_card or {}).get("cost"))
        cost = summary_cost if summary_cost is not None else card_cost
        if cost is None:
            return False
        return cost <= money

    if shop_cache is not None:
        voucher_cards = list(shop_cache.voucher_cards)
        voucher_summaries = list(shop_cache.voucher_summaries)
        pack_cards = list(shop_cache.pack_cards)
        pack_summaries = list(shop_cache.pack_summaries)
    else:
        voucher_cards = _get_state_cards(raw_state, "vouchers")
        voucher_summaries = [
            _summarize_shop_item(card, raw_state, deck_name) for card in voucher_cards
        ]
        pack_cards = _get_state_cards(raw_state, "packs")
        pack_summaries = [
            _summarize_shop_item(card, raw_state, deck_name) for card in pack_cards
        ]

    for idx, card in enumerate(voucher_cards, start=1):
        summary = (
            dict(voucher_summaries[idx - 1])
            if idx - 1 < len(voucher_summaries)
            else _summarize_shop_item(card, raw_state, deck_name)
        )
        if _advisor_not_bad(summary) and _is_affordable(summary, card):
            return ("buy_voucher", idx, summary)

    for idx, card in enumerate(pack_cards, start=1):
        summary = (
            dict(pack_summaries[idx - 1])
            if idx - 1 < len(pack_summaries)
            else _summarize_shop_item(card, raw_state, deck_name)
        )
        if _advisor_not_bad(summary) and _is_affordable(summary, card):
            return ("buy_pack", idx, summary)

    return None


def _log_survival_gate_blocked_safe(
    raw_state: dict,
    target_joker: str,
    sacrificed_joker: str,
    current_immediate_score: int,
    new_immediate_score: int,
    gated_requirement: int,
    new_future_score: int,
    reason: str,
) -> None:
    """Best-effort telemetry for survival-gate blocked decisions."""
    try:
        from skills.balatro_bot.modules.balatro_telemetry import (
            log_survival_gate_blocked,
        )

        log_survival_gate_blocked(
            raw_state=raw_state,
            target_joker=target_joker,
            sacrificed_joker=sacrificed_joker,
            current_immediate_score=current_immediate_score,
            new_immediate_score=new_immediate_score,
            gated_requirement=gated_requirement,
            new_future_score=new_future_score,
            reason=reason,
        )
    except Exception as exc:
        log_system(f"[Balatro] Survival gate telemetry failed: {exc}")


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
        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        shop_cache = _get_tick_shop_cache(ctx, deck_name)

        # Get current shop size for bounds checking
        shop_cards = (
            list(shop_cache.shop_cards)
            if shop_cache is not None
            else _get_state_cards(raw_state, "shop")
        )
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
        if (
            shop_cache is not None
            and 0 <= target_index < len(shop_cache.shop_summaries)
        ):
            shop_summary = dict(shop_cache.shop_summaries[target_index])
        else:
            shop_summary = _summarize_shop_item(shop_card, raw_state, deck_name)
        replace_hint = str(shop_summary.get("replace_idx") or "").strip()
        slot_error = _build_dynamic_slot_full_error(
            shop_card,
            raw_state,
            replace_hint=replace_hint,
        )
        if slot_error:
            self._log_error(ctx, slot_error)
            return ActionResult(
                succeeded=False,
                error=slot_error,
                persona_reasoning=slot_error,
            )

        reserve_block_reason = _early_game_buy_block_reason(
            raw_state,
            shop_card,
            deck_name,
            shop_summary=shop_summary,
        )
        if reserve_block_reason:
            error = f"Action Denied: {reserve_block_reason}"
            persona_reasoning = (
                f"Tried to buy {shop_summary['label']} for "
                f"${format_cost_for_display(shop_summary['cost'])}, but I need to keep "
                f"${EARLY_GAME_INTEREST_RESERVE} banked after the survival buy."
            )
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False, error=error, persona_reasoning=persona_reasoning
            )

        advisor_core = _extract_advisor_core(str(shop_summary.get("advisor") or ""))
        if advisor_core == "BAD SYNERGY":
            error = (
                "Action Denied: Purchase blocked by safety override (BAD SYNERGY). "
                "Pick a different action."
            )
            persona_reasoning = f"Skipping {shop_summary['label']} because it is BAD SYNERGY for this build."
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False,
                error=error,
                persona_reasoning=persona_reasoning,
            )

        money_now = _safe_int(raw_state.get("money"), 0)
        economy_tag = str(shop_summary.get("economy_tag") or "")
        if (
            BalatroAlgorithm.is_probable_joker(shop_card)
            and money_now >= EARLY_GAME_INTEREST_RESERVE
            and "[CRITICAL UPGRADE]" not in economy_tag
        ):
            non_rerollable_option = _find_affordable_non_rerollable_option(
                raw_state=raw_state,
                money=money_now,
                deck_name=deck_name,
                shop_cache=shop_cache,
            )
            if non_rerollable_option is not None:
                next_action, next_idx, next_summary = non_rerollable_option
                next_label = str(next_summary.get("label") or "item")
                error = (
                    "Action Denied: Non-rerollable priority rule. "
                    f"Buy {next_label} first via {next_action} [{next_idx}] "
                    "before buying a non-critical joker while reserve is intact."
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False,
                    error=error,
                    persona_reasoning=error,
                )

        # Survival-gated pivoting: require immediate board viability, with extra boss margin.
        if BalatroAlgorithm.is_probable_joker(shop_card):
            current_jokers = (
                list(shop_cache.jokers)
                if shop_cache is not None
                else _get_state_cards(raw_state, "jokers")
            )
            immediate_expected = int(shop_summary.get("projected_score", 0) or 0)
            future_expected = int(shop_summary.get("expected_score", 0) or 0)
            current_immediate_score = int(shop_summary.get("baseline_score", 0) or 0)

            current_blind_requirement = 0
            blinds = raw_state.get("blinds") or {}
            if isinstance(blinds, dict):
                for key in ("small", "big", "boss"):
                    blind = blinds.get(key) or {}
                    if not isinstance(blind, dict):
                        continue
                    status = str(blind.get("status", "")).lower()
                    if status in ("active", "current", "playing"):
                        current_blind_requirement = _safe_int(
                            blind.get("score") or blind.get("chips_required"),
                            0,
                        )
                        break

            boss_mult = _shop_boss_requirement_multiplier(raw_state)
            ante = _safe_int(raw_state.get("ante_num"), 1)
            margin = BLIND_SAFETY_MARGIN_BY_ANTE.get(ante, 1.0)
            gated_requirement = int(round(current_blind_requirement * boss_mult * margin))
            is_strict_immediate_upgrade = immediate_expected > current_immediate_score
            if (
                gated_requirement > 0
                and immediate_expected < gated_requirement
                and not is_strict_immediate_upgrade
            ):
                error = (
                    "Action Denied: Pivot blocked by survival gate. "
                    f"Immediate expected score ({immediate_expected}) is below "
                    f"required threshold ({gated_requirement})."
                )
                replacement_index = shop_summary.get("replacement_index")
                sacrificed_joker = ""
                if isinstance(replacement_index, int) and replacement_index >= 1:
                    idx0 = replacement_index - 1
                    if 0 <= idx0 < len(current_jokers):
                        sacrificed_joker = str(
                            current_jokers[idx0].get("label")
                            or current_jokers[idx0].get("key")
                            or ""
                        )
                _log_survival_gate_blocked_safe(
                    raw_state=raw_state,
                    target_joker=str(
                        shop_card.get("label") or shop_card.get("key") or ""
                    ),
                    sacrificed_joker=sacrificed_joker,
                    current_immediate_score=current_immediate_score,
                    new_immediate_score=immediate_expected,
                    gated_requirement=gated_requirement,
                    new_future_score=future_expected,
                    reason="below_boss_gated_requirement_without_immediate_upgrade",
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False,
                    error=error,
                    persona_reasoning=error,
                )

            baseline_expected = int(shop_summary.get("baseline_score", 0) or 0)
            if (
                future_expected <= baseline_expected
                and shop_summary.get("replacement_index") is not None
            ):
                error = (
                    "Action Denied: Pivot blocked. 3-round projection does not improve "
                    f"current board ({future_expected} <= {baseline_expected})."
                )
                replacement_index = shop_summary.get("replacement_index")
                sacrificed_joker = ""
                if isinstance(replacement_index, int) and replacement_index >= 1:
                    idx0 = replacement_index - 1
                    if 0 <= idx0 < len(current_jokers):
                        sacrificed_joker = str(
                            current_jokers[idx0].get("label")
                            or current_jokers[idx0].get("key")
                            or ""
                        )
                _log_survival_gate_blocked_safe(
                    raw_state=raw_state,
                    target_joker=str(
                        shop_card.get("label") or shop_card.get("key") or ""
                    ),
                    sacrificed_joker=sacrificed_joker,
                    current_immediate_score=current_immediate_score,
                    new_immediate_score=immediate_expected,
                    gated_requirement=gated_requirement,
                    new_future_score=future_expected,
                    reason="future_projection_not_improving",
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False,
                    error=error,
                    persona_reasoning=error,
                )

        # Execute buy using 0-based index
        log_system(
            "[Balatro] Buying shop item "
            f"#{card_idx}: {_format_shop_item_summary(shop_summary)}."
        )
        result = ctx.controller.client.buy(card=target_index)

        if not result:
            api_error = str(getattr(ctx.controller.client, "_last_error", "") or "")
            if (
                "slots are full" in api_error.lower()
                or "slot full" in api_error.lower()
            ):
                error = (
                    f"Shop purchase failed due to slot constraints. API said: {api_error}"
                    if api_error
                    else "Error: Shop purchase failed due to slot constraints."
                )
                persona_reasoning = error
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False, error=error, persona_reasoning=persona_reasoning
                )

            error = "Insufficient funds or invalid item."
            persona_reasoning = (
                f"Tried to buy {shop_summary['label']} for "
                f"${format_cost_for_display(shop_summary['cost'])}, but the shop rejected it."
            )

            # Refresh state on failed mutation before deciding whether alternatives are affordable.
            fresh_state = raw_state
            try:
                ctx.controller.refresh_state()
                controller_state = getattr(ctx.controller, "raw_state", None)
                if isinstance(controller_state, dict) and controller_state:
                    fresh_state = controller_state
                    ctx.tick_context.update_raw_state(controller_state)
            except Exception:
                fresh_state = raw_state

            # Only advance past the shop if nothing else in it is affordable.
            # If cheaper items remain, let the planner re-evaluate rather than wasting the shop phase.
            money = _safe_int(fresh_state.get("money"), 0)
            numeric_cost = _extract_cost_value(shop_summary.get("cost"))
            if numeric_cost is not None and numeric_cost > money:
                # Confirmed insufficient funds — annotate the error so planner remembers
                error = (
                    f"Insufficient funds: {shop_summary['label']} costs "
                    f"${numeric_cost} but you only have ${money}. Try a cheaper item or continue."
                )
                persona_reasoning = error

            if _any_affordable(fresh_state, money):
                log_system(
                    f"[Balatro] Buy failed ({error}). "
                    "Cheaper items remain — letting planner re-evaluate instead of forcing proceed_next."
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False, error=error, persona_reasoning=persona_reasoning
                )

            log_system(
                "[Balatro] Buy failed (insufficient funds or invalid item). "
                "Nothing else affordable — forcing next round to prevent soft-lock."
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
        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        shop_cache = _get_tick_shop_cache(ctx, deck_name)

        # Get pack card
        pack_card = None
        pack_cards = (
            list(shop_cache.pack_cards)
            if shop_cache is not None
            else _get_state_cards(raw_state, "packs")
        )
        zero_idx = pack_idx - 1
        if 0 <= zero_idx < len(pack_cards):
            pack_card = pack_cards[zero_idx]

        if (
            shop_cache is not None
            and 0 <= zero_idx < len(shop_cache.pack_summaries)
        ):
            pack_summary = dict(shop_cache.pack_summaries[zero_idx])
        else:
            pack_summary = _summarize_shop_item(pack_card, raw_state, deck_name)
        advisor_core = _extract_advisor_core(str(pack_summary.get("advisor") or ""))
        if advisor_core == "BAD SYNERGY":
            error = (
                "Action Denied: Purchase blocked by safety override (BAD SYNERGY). "
                "Pick a different action."
            )
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False,
                error=error,
                persona_reasoning="Skipping this pack because it is marked BAD SYNERGY.",
            )
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
        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        shop_cache = _get_tick_shop_cache(ctx, deck_name)

        # Get voucher card
        voucher_card = None
        voucher_cards = (
            list(shop_cache.voucher_cards)
            if shop_cache is not None
            else _get_state_cards(raw_state, "vouchers")
        )
        if voucher_cards:
            voucher_card = voucher_cards[0]

        if shop_cache is not None and shop_cache.voucher_summaries:
            voucher_summary = dict(shop_cache.voucher_summaries[0])
        else:
            voucher_summary = _summarize_shop_item(voucher_card, raw_state, deck_name)
        advisor_core = _extract_advisor_core(str(voucher_summary.get("advisor") or ""))
        if advisor_core == "BAD SYNERGY":
            error = (
                "Action Denied: Purchase blocked by safety override (BAD SYNERGY). "
                "Pick a different action."
            )
            self._log_error(ctx, error)
            return ActionResult(
                succeeded=False,
                error=error,
                persona_reasoning="Skipping this voucher because it is marked BAD SYNERGY.",
            )
        log_system(
            f"[Balatro] Buying voucher: {_format_shop_item_summary(voucher_summary)}."
        )

        # Execute buy — voucher API is 0-based; there is always at most one voucher slot.
        result = ctx.controller.client.buy(voucher=0)

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

        try:
            money = int(money)
        except (TypeError, ValueError):
            money = 0
        try:
            reroll_cost = int(reroll_cost)
        except (TypeError, ValueError):
            reroll_cost = 0

        post_reroll_money = money - reroll_cost
        ante = _safe_int(raw_state.get("ante_num"), 1)
        reserve_target = _get_reserve_for_ante(ante, money)
        # Use the lower of the hard floor and the phase-aware reserve target so
        # Mid/Late-game reserves ($10/$5) are not silently overridden by $15.
        absolute_floor = min(CRITICAL_SPEND_FLOOR, reserve_target)

        if post_reroll_money < absolute_floor:
            error = (
                "Action Denied: Reroll blocked by economy floor "
                f"(would leave ${post_reroll_money}, floor is ${absolute_floor}). "
                "Pick a different action."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning=error)

        # If a strong item is already visible, do not burn money rerolling away from it,
        # unless the full shop is clearly weak/low and reroll is explicitly allowed.
        deck_name = (
            ctx.session.run_profile.get("deck", "") if ctx.session.run_profile else ""
        )
        shop_cache = _get_tick_shop_cache(ctx, deck_name)
        if shop_cache is not None:
            summaries = list(shop_cache.shop_summaries)
        else:
            summaries = [
                _summarize_shop_item(card, raw_state, deck_name)
                for card in _get_state_cards(raw_state, "shop")
            ]

        def _is_low_or_bad(summary: dict) -> bool:
            advisor = _extract_advisor_core(str(summary.get("advisor") or ""))
            impact = float(summary.get("impact_pct") or 0.0)
            return (
                advisor in ("BAD SYNERGY", "LOW VALUE", "SITUATIONAL", "")
                and impact < 12.0
            )

        all_bad_or_low = bool(summaries) and all(
            _is_low_or_bad(summary) for summary in summaries
        )

        jokers_container = raw_state.get("jokers") or {}
        joker_slots_total = _safe_int(
            jokers_container.get("size") if isinstance(jokers_container, dict) else 0,
            5,
        )
        joker_slots_used = len(
            jokers_container.get("cards", [])
            if isinstance(jokers_container, dict)
            else []
        )
        joker_slots_free = max(0, joker_slots_total - joker_slots_used)
        weak_synergy = _count_survival_jokers(raw_state) < EARLY_GAME_JOKER_TARGET

        survival_exception = (
            post_reroll_money < reserve_target
            and post_reroll_money >= absolute_floor
            and joker_slots_free > 0
            and weak_synergy
        )

        if post_reroll_money < reserve_target and not survival_exception:
            error = (
                "Action Denied: Reroll blocked by reserve rule "
                f"(would leave ${post_reroll_money}, target is ${reserve_target}). "
                "You may dip below $25 only when joker slots are open and synergy is weak."
            )
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning=error)

        if post_reroll_money < reserve_target and survival_exception:
            log_system(
                "[Balatro] Reroll survival exception active: weak synergy + free joker slot."
            )

        if money >= reserve_target:
            non_rerollable_option = _find_affordable_non_rerollable_option(
                raw_state=raw_state,
                money=money,
                deck_name=deck_name,
                shop_cache=shop_cache,
            )
            if non_rerollable_option is not None:
                next_action, next_idx, next_summary = non_rerollable_option
                next_label = str(next_summary.get("label") or "item")
                error = (
                    "Action Denied: Reroll blocked by non-rerollable priority. "
                    f"Buy {next_label} first via {next_action} [{next_idx}] "
                    "while reserve is intact."
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False, error=error, persona_reasoning=error
                )

        for idx, summary in enumerate(summaries, start=1):
            advisor = _extract_advisor_core(str(summary.get("advisor") or ""))
            impact = float(summary.get("impact_pct") or 0.0)
            if not all_bad_or_low and (advisor == "HIGH SYNERGY" or impact >= 15.0):
                label = str(summary.get("label") or f"item #{idx}")
                error = (
                    "Action Denied: Reroll blocked because a high-value shop item is already present "
                    f"({_format_shop_item_summary(summary)}). "
                    f"Buy it now with buy_shop [{idx}] ({label}) or continue instead."
                )
                self._log_error(ctx, error)
                return ActionResult(
                    succeeded=False, error=error, persona_reasoning=error
                )

        log_system(f"[Balatro] Rerolling shop for ${reroll_cost}.")
        result = ctx.controller.client.reroll()

        if not result:
            api_error = getattr(
                ctx.controller.client, "_last_error", "Unknown API error"
            )
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
