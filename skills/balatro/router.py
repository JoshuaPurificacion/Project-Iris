import dataclasses
from typing import TYPE_CHECKING, Any, Optional

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
    def get_directive(tick_context: "TickContext") -> Optional[PhaseDirective]:
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
                "Pick a deck to start a new run. "
                f"CRITICAL: You MUST output a 'deck' field with your chosen deck name. "
                "STRATEGIC GOAL: ALWAYS choose CHECKERED for consistent flush-based clears. "
                "Do not experiment with other decks for now. "
                "Populate the 'deck' field with CHECKERED. "
                f"Leave indices empty."
            ),
        )

    @staticmethod
    def _get_blind_directive() -> PhaseDirective:
        """Blind selection directive."""
        return PhaseDirective(
            phase_id="blind",
            allowed_actions=["select", "skip_blind", "skip"],
            guidance=(
                "ALWAYS select the blind. Output action='select' with empty indices. "
                "NOTE: The tag shown next to a blind (e.g. 'Rare Joker Tag') is a SKIP reward — "
                "you only get it by using skip_blind, NOT by selecting. "
                "Always select regardless of what tag is shown."
            ),
        )

    @staticmethod
    def _get_round_eval_directive() -> PhaseDirective:
        """Round evaluation directive."""
        return PhaseDirective(
            phase_id="round_eval",
            allowed_actions=["cash_out", "cashout", "cash"],
            guidance="Round over. Cash out immediately.",
        )

    @staticmethod
    def _get_shop_directive(tick_context: "TickContext", ante: int) -> PhaseDirective:
        """Shop directive with early/mid/late game guidance."""
        from .session import (
            _has_survival_joker,
            _get_state_cards,
            _summarize_shop_item,
            _format_shop_item_summary,
            _safe_int,
            EARLY_GAME_ANTE_LIMIT,
            EARLY_GAME_INTEREST_RESERVE,
        )
        from core.logger import log_system

        raw_state = tick_context.raw_state

        # --- Log all current shop items ---
        shop_cards = _get_state_cards(raw_state, "shop")
        deck_name = ""  # deck not needed for label/cost display here
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
        voucher_cards = _get_state_cards(raw_state, "vouchers")
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

        if ante <= EARLY_GAME_ANTE_LIMIT and not has_survival_joker:
            guidance = (
                f"EARLY GAME: If you do NOT already own a survival Joker, buy one "
                f"Joker that directly adds Chips, Mult, or xMult, even if it "
                f"drops you below ${EARLY_GAME_INTEREST_RESERVE}. "
                f"CRITICAL: To buy, you MUST use action 'buy_shop' and put the 1-based item number in the 'indices' array (e.g., indices: [1]). "
                f"After that first survival Joker, protect "
                f"${EARLY_GAME_INTEREST_RESERVE} and spend only the excess above it "
                "on build Jokers. If joker slots are full, sell_joker a low-impact joker first, then buy the upgrade. "
                "If nothing good fits the excess budget, use reroll if affordable, else continue."
            )
        elif ante <= EARLY_GAME_ANTE_LIMIT:
            guidance = (
                f"EARLY GAME: You already have a survival Joker. Keep at least "
                f"${EARLY_GAME_INTEREST_RESERVE} banked and spend the excess above it. "
                "SPENDING ORDER: (1) Buy Jokers that add Chips, Mult, or xMult. (2) Buy packs if affordable. (3) Reroll if you have excess money and still need better jokers. "
                "If joker slots are full, sell_joker only low-impact jokers to make room for stronger upgrades. "
                "CRITICAL: To buy a shop item, use action 'buy_shop' with 1-based index (e.g., indices: [1]). "
                "To buy a pack, use action 'buy_pack' with 1-based index (e.g., indices: [1]). "
                "You may also use non-targeted consumables (Planets, Hermit, etc) using action 'use_consumable' with just its index. Do NOT use targeted consumables outside of blinds. "
                "If nothing good fits the budget, use reroll or continue."
            )
        else:
            guidance = (
                "MID/LATE GAME: Keep at least $25 for max interest. "
                "SPENDING ORDER: (1) Use any Tarot/Planet consumables you have for free value - Planets scale your most-played hand, Tarots give economy/synergy. "
                "(2) Buy Planet card matching your highest-scoring hand type. (3) Buy Tarot cards. "
                "(4) Spend excess on Joker builds (xMult, Chips, Mult). (5) Buy packs if affordable. "
                "(6) Reroll shop if sitting on $25+ and need to optimize Joker loadout. "
                "If joker slots are full, sell_joker only when replacing with a higher-impact joker. "
                "CRITICAL: To buy a pack, use action 'buy_pack' with 1-based index (e.g., indices: [1]). "
                "To use consumables, use action 'use_consumable' with index (e.g., indices: [1]). "
                "You may also use non-targeted consumables (Planets, Hermit, etc) using action 'use_consumable' with just its index. Do NOT use targeted consumables outside of blinds. "
                "To reroll the shop, use action 'reroll'."
            )

        availability_rules: list[str] = []
        if not can_buy_shop:
            availability_rules.append("No shop cards available. Do NOT output buy_shop.")
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
            [can_buy_shop, can_buy_pack, can_buy_voucher, can_reroll, can_use_consumable]
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
