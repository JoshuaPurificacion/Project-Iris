import dataclasses
from typing import TYPE_CHECKING, Any
from .utils import _get_state_cards, _safe_int

if TYPE_CHECKING:
    from .session import TickContext


@dataclasses.dataclass
class PhaseDirective:
    phase_id: str
    allowed_actions: list[str]
    guidance: str
    utility_actions: list[str] = dataclasses.field(
        default_factory=lambda: [
            "sell_joker",
            "sell_consumable",
            "use_consumable",
            "rearrange_joker",
            "rearrange_consumable",
        ]
    )

    @property
    def full_allowed_actions(self) -> list[str]:
        """Return allowed actions plus utility actions."""
        return list(set(self.allowed_actions + self.utility_actions))


class PhaseRouter:
    """Stateless router that evaluates TickContext to determine current phase."""

    @staticmethod
    def get_directive(tick_context: "TickContext") -> PhaseDirective | None:
        """Return the PhaseDirective for the current game state."""
        raw_state = tick_context.raw_state
        current_state = (raw_state.get("state", "") or "").lower()
        ante = raw_state.get("ante_num", 1)

        # Menu phase
        if "menu" in current_state:
            return PhaseRouter._get_menu_directive()

        # Game over phase
        if "game_over" in current_state:
            return None  # Handled specially in session

        # Blind phase
        if "blind" in current_state:
            return PhaseRouter._get_blind_directive()

        # Round evaluation phase
        if "round_eval" in current_state:
            return PhaseRouter._get_round_eval_directive()

        # Shop phase
        if "shop" in current_state:
            return PhaseRouter._get_shop_directive(tick_context, ante)

        # Pack/booster phase
        if "pack" in current_state or "booster" in current_state:
            return PhaseRouter._get_pack_directive(raw_state)

        # Play/hand/discard phase (default)
        return PhaseRouter._get_play_discard_directive(raw_state)

    @staticmethod
    def _get_menu_directive() -> PhaseDirective:
        """Menu state directive."""
        return PhaseDirective(
            phase_id="menu",
            allowed_actions=["start_run"],
            guidance=(
                "Start a new run immediately. "
                "STRATEGIC GOAL: CHECKERED-only consistency mode is enforced by backend. "
                "Output action='start_run' with empty indices."
            ),
        )

    @staticmethod
    def _get_blind_directive() -> PhaseDirective:
        """Blind selection directive."""
        return PhaseDirective(
            phase_id="blind",
            allowed_actions=["select"],
            guidance=(
                "ALWAYS select the blind. Output action='select' with empty indices. "
                "Do not skip blinds."
            ),
        )

    @staticmethod
    def _get_round_eval_directive() -> PhaseDirective:
        """Round evaluation directive."""
        return PhaseDirective(
            phase_id="round_eval",
            allowed_actions=["cash_out"],
            # Override utility_actions to remove use_consumable — the game API
            # does not allow it during round_eval and it causes softlocks.
            utility_actions=[
                "sell_joker",
                "sell_consumable",
                "rearrange_joker",
                "rearrange_consumable",
            ],
            guidance=(
                "ROUND EVALUATION: You just beat the blind. "
                "The shop is not open yet. "
                "Output action='cash_out' with empty indices immediately. "
                "Do NOT attempt buy_shop, reroll, use_consumable, or any shop action."
            ),
        )

    @staticmethod
    def _get_shop_directive(tick_context: "TickContext", ante: int) -> PhaseDirective:
        """Shop directive with early/mid/late game guidance."""
        from .shop_analysis import (
            _has_survival_joker,
            _count_survival_jokers,
            _is_buffoon_pack,
            _extract_advisor_core,
            _summarize_shop_item,
            _format_shop_item_summary,
            EARLY_GAME_ANTE_LIMIT,
            EARLY_GAME_INTEREST_RESERVE,
            CONSUMABLE_EXCEPTION_FLOOR,
        )
        from core.logger import log_system

        raw_state = tick_context.raw_state

        # --- Log all current shop items ---
        shop_cards = _get_state_cards(raw_state, "shop")
        deck_name = str(raw_state.get("deck") or "")
        if shop_cards:
            lines = []
            for i, card in enumerate(shop_cards, start=1):
                summary = _summarize_shop_item(card, raw_state, deck_name)
                lines.append(f"  [{i}] {_format_shop_item_summary(summary)}")
            log_system("[Balatro] Shop inventory:\n" + "\n".join(lines))
        else:
            log_system("[Balatro] Shop inventory: (empty)")
        has_survival_joker = _has_survival_joker(raw_state)

        pack_cards = _get_state_cards(raw_state, "packs")
        if pack_cards:
            pack_lines = []
            for i, card in enumerate(pack_cards, start=1):
                summary = _summarize_shop_item(card, raw_state, deck_name)
                pack_lines.append(f"  [{i}] {_format_shop_item_summary(summary)}")
            log_system("[Balatro] Booster Packs:\n" + "\n".join(pack_lines))
        else:
            log_system("[Balatro] Booster Packs: (empty)")

        voucher_cards = _get_state_cards(raw_state, "vouchers")
        if voucher_cards:
            voucher_lines = []
            for i, card in enumerate(voucher_cards, start=1):
                summary = _summarize_shop_item(card, raw_state, deck_name)
                voucher_lines.append(f"  [{i}] {_format_shop_item_summary(summary)}")
            log_system("[Balatro] Vouchers:\n" + "\n".join(voucher_lines))
        else:
            log_system("[Balatro] Vouchers: (empty)")

        consumable_cards = _get_state_cards(raw_state, "consumeables")
        money = _safe_int(raw_state.get("money"), 0)
        round_info = raw_state.get("round") or {}
        reroll_cost = (
            _safe_int(round_info.get("reroll_cost"), 0)
            if isinstance(round_info, dict)
            else 0
        )
        can_reroll = reroll_cost > 0 and money >= reroll_cost

        can_buy_shop = bool(shop_cards)
        can_buy_pack = bool(pack_cards)
        can_buy_voucher = bool(voucher_cards)
        can_use_consumable = bool(consumable_cards)
        survival_count = _count_survival_jokers(raw_state)
        has_early_buffoon_pack = ante <= EARLY_GAME_ANTE_LIMIT and any(
            _is_buffoon_pack(card) for card in pack_cards
        )

        shop_summaries = [
            _summarize_shop_item(card, raw_state, deck_name) for card in shop_cards
        ]
        has_bad_synergy_item = any(
            _extract_advisor_core(str(summary.get("advisor") or "")) == "BAD SYNERGY"
            for summary in shop_summaries
        )
        all_bad_or_low_shop = bool(shop_summaries) and all(
            _extract_advisor_core(str(summary.get("advisor") or ""))
            in ("BAD SYNERGY", "LOW VALUE", "SITUATIONAL", "")
            for summary in shop_summaries
        )

        if ante <= EARLY_GAME_ANTE_LIMIT and not has_survival_joker:
            guidance = (
                "EARLY GAME PRIORITY: Rule 1 survive next blind. Rule 2 keep $25+ interest when possible. "
                f"You currently have {survival_count} scoring Jokers. "
                "If a Buffoon pack is available, prioritize it immediately to find Chips/Mult/xMult support. "
                "If no Buffoon is available, buy the best available scoring upgrade now. "
                f"You may dip below ${EARLY_GAME_INTEREST_RESERVE} for survival-critical upgrades. "
                f"CRITICAL: To buy, you MUST use action 'buy_shop' and put the 1-based item number in the 'indices' array (e.g., indices: [1]). "
                "Respect item tags: [SAFE TO BUY], [CRITICAL UPGRADE], [COSTS INTEREST]. "
                "If joker slots are full, sell only jokers tagged [SAFE TO SELL] before buying upgrades."
            )
        elif ante <= EARLY_GAME_ANTE_LIMIT:
            guidance = (
                "EARLY GAME: Rule 1 survive next blind, Rule 2 maintain $25+ when possible. "
                f"Interest target is ${EARLY_GAME_INTEREST_RESERVE}, but survival upgrades can dip lower. "
                "SPENDING ORDER: (1) vouchers, (2) packs (including Tarot/Planet sources), (3) Tarot/Planet support cards, (4) scoring Joker upgrades, (5) reroll only when no good non-rerollables remain. "
                f"Consumable exception: you may dip to ${CONSUMABLE_EXCEPTION_FLOOR} for impactful Tarot/Planet buys. "
                "NON-REROLLABLE PRIORITY: if an affordable voucher or pack exists, buy it before buy_shop or reroll unless the Joker is a [CRITICAL UPGRADE]. "
                "If joker slots are full, sell_joker only cards marked [SAFE TO SELL]. "
                "CRITICAL: To buy a shop item, use action 'buy_shop' with 1-based index (e.g., indices: [1]). "
                "To buy a pack, use action 'buy_pack' with 1-based index (e.g., indices: [1]). "
                "You may also use non-targeted consumables (Planets, Hermit, etc) using action 'use_consumable' with just its index. Do NOT use targeted consumables outside of blinds. "
                "If all shop cards are low/bad and reroll keeps economy floor, reroll is valid."
            )
        else:
            guidance = (
                "MID/LATE GAME: Rule 1 survive next blind. Rule 2 keep $25+ interest when possible. "
                "Use item tags to choose buys: [SAFE TO BUY] first, then [CRITICAL UPGRADE] when survival/scaling needs it, and avoid [COSTS INTEREST] unless needed. "
                "SPENDING ORDER: (1) vouchers, (2) packs, (3) Tarot/Planet support, (4) strongest Joker upgrade, (5) reroll only when no good non-rerollables remain. "
                f"You may dip to ${CONSUMABLE_EXCEPTION_FLOOR} for impactful Tarot/Planet cards. "
                "NON-REROLLABLE PRIORITY: if an affordable voucher or pack exists, buy it before buy_shop or reroll unless the Joker is a [CRITICAL UPGRADE]. "
                "If joker slots are full, swap only via jokers tagged [SAFE TO SELL]. "
                "CRITICAL: To buy a pack, use action 'buy_pack' with 1-based index (e.g., indices: [1]). "
                "To use consumables, use action 'use_consumable' with index (e.g., indices: [1]). "
                "You may also use non-targeted consumables (Planets, Hermit, etc) using action 'use_consumable' with just its index. Do NOT use targeted consumables outside of blinds. "
                "To reroll the shop, use action 'reroll'."
            )

        if has_early_buffoon_pack and survival_count == 0:
            guidance += " URGENT: No scoring Joker online yet and Buffoon is available - buy_pack now."
        if has_bad_synergy_item:
            guidance += " BAD SYNERGY items are hard-blocked by backend; never pick them and skip them entirely."
            guidance += " Unreliable chance/random/self-destruct jokers are classified BAD SYNERGY for Checkered runs."
        if all_bad_or_low_shop:
            guidance += " Shop quality is weak overall; reroll is preferred when economy floor allows."

        availability_rules: list[str] = []
        if not can_buy_shop:
            availability_rules.append(
                "No shop cards available. Do NOT output buy_shop."
            )
        if not can_buy_pack:
            availability_rules.append("No packs available. Do NOT output buy_pack.")
        if not can_buy_voucher:
            availability_rules.append(
                "No voucher available. Do NOT output buy_voucher."
            )
        if not can_reroll:
            availability_rules.append(
                f"Reroll unavailable or unaffordable (money=${money}, reroll_cost=${reroll_cost}). Do NOT output reroll."
            )

        no_shop_progression_action = not any(
            [
                can_buy_shop,
                can_buy_pack,
                can_buy_voucher,
                can_reroll,
                can_use_consumable,
            ]
        )
        if no_shop_progression_action:
            availability_rules.append(
                "No valid shop action besides continue is available. Output action='continue' with empty indices."
            )

        if availability_rules:
            guidance = guidance + " " + " ".join(availability_rules)

        guidance = (
            guidance
            + " SHOP DECISION RULE: Before selecting any shop action, populate the output field 'synergy_evaluation' by comparing each candidate Joker/shop card value.effect against your currently owned Joker value.effect text and current build direction (chips vs mult vs xmult, hand-type conditions). Then pick action/indices based on that fit analysis."
        )

        allowed_actions = ["continue"]
        if can_buy_shop:
            allowed_actions.append("buy_shop")
        if can_buy_pack:
            allowed_actions.append("buy_pack")
        if can_buy_voucher:
            allowed_actions.append("buy_voucher")
        if can_reroll:
            allowed_actions.append("reroll")
        if can_use_consumable:
            allowed_actions.append("use_consumable")

        utility_actions = [
            "sell_joker",
            "sell_consumable",
            "rearrange_joker",
            "rearrange_consumable",
        ]
        if can_use_consumable:
            utility_actions.append("use_consumable")

        return PhaseDirective(
            phase_id="shop",
            allowed_actions=allowed_actions,
            utility_actions=utility_actions,
            guidance=guidance,
        )

    @staticmethod
    def _get_pack_directive(raw_state: dict[str, Any]) -> PhaseDirective:
        """Pack/booster selection directive."""
        pack_data = raw_state.get("pack", {})
        choices_left = (
            pack_data.get("choices_left", 1) if isinstance(pack_data, dict) else 1
        )

        return PhaseDirective(
            phase_id="pack",
            allowed_actions=["choose_pack", "skip_pack"],
            utility_actions=[],
            guidance=(
                f"Pack opened. Picks remaining: {choices_left}. "
                "Choose the best card for your synergies. "
                "For choose_pack: indices[0]=pack card index, indices[1+]=1-based hand targets if needed."
            ),
        )

    @staticmethod
    def _get_play_discard_directive(raw_state: dict[str, Any]) -> PhaseDirective:
        """Play/discard phase directive."""
        round_data = raw_state.get("round", {})
        discards_left = (
            round_data.get("discards_left", 0) if isinstance(round_data, dict) else 0
        )

        if discards_left > 0:
            # Add greed warning for last discard
            greed_warning = (
                (
                    " CRITICAL: Do NOT use your last discard unless you have 0 valid scoring hands. "
                    "Prefer playing a low-tier hand (Pair/High Card) over burning your final discard."
                )
                if discards_left == 1
                else ""
            )

            guidance = (
                f"Play or discard. Discards left: {discards_left}.{greed_warning} "
                "Use algorithm hints above. "
                "For play_hand: indices = 1-based card positions to play. "
                "For discard: indices = 1-based card positions to discard. "
                "If you have a targeted consumable (like an enhancement Tarot), use 'use_consumable' with its index followed by the target card indices (e.g., indices: [1, 2, 4])."
            )
            allowed_actions = ["play_hand", "discard"]
        else:
            guidance = (
                "0 DISCARDS LEFT! You MUST play a hand. Do not attempt to discard. "
                "For play_hand: indices = 1-based card positions to play. "
                "If you have a targeted consumable (like an enhancement Tarot), use 'use_consumable' with its index followed by the target card indices (e.g., indices: [1, 2, 4])."
            )
            allowed_actions = ["play_hand"]

        return PhaseDirective(
            phase_id="play_discard", allowed_actions=allowed_actions, guidance=guidance
        )
