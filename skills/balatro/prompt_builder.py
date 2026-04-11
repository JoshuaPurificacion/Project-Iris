import typing
from typing import TYPE_CHECKING
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm
from .shop_analysis import (
    _count_survival_jokers,
    _extract_advisor_core,
    _is_buffoon_pack,
    _summarize_shop_item,
    EARLY_GAME_JOKER_TARGET,
    EARLY_GAME_INTEREST_RESERVE,
    EARLY_GAME_ANTE_LIMIT,
    CONSUMABLE_EXCEPTION_FLOOR,
)
from .utils import _get_state_cards, _safe_int

if TYPE_CHECKING:
    from .session import TickContext
from .router import PhaseDirective


DECK_STRATEGIES: dict[str, str] = {
    "RED": "+1 discard per round - use extra discards to hunt high-tier hands",
    "BLUE": "Safety: +1 hand/round - play high-volume to survive early, +$1/round",
    "YELLOW": "Economy: +$10 start - hit $25 interest cap fast, invest early",
    "GREEN": "Volume: $2/hand + $1/discard - finish rounds fast for max payout",
    "BLACK": "Power: +1 joker slot, -1 hand - long-term scaling, risky early",
    "MAGIC": "Consumable: +1 slot + 2x Fool - duplicate Tarots for scaling",
    "NEBULA": "Telescope: Planet packs show your most played hand - focus one type",
    "GHOST": "Risk: Spectral in shop + Hex - high variance, high reward",
    "ABANDONED": "No face cards - Straights and low-rank synergies easier. Prefer reliable scoring over delayed scaler jokers.",
    "CHECKERED": "Synergy: Flush only (26♠ 26♥) - suit Jokers priority (Heart/Spade). PRIORITY: Jupiter Planet, Bloodstone, Castle, Arrowhead.",
    "ZODIAC": "Merchant: Tarot/Planet/Overstock appear more - save money for shop",
    "PAINTED": "Size: +2 hand size, -1 joker slot (4 total) - big hands priority",
    "ANAGLYPH": "Double Tags: Boss blinds grant 2 tags - skip for mega rewards",
    "PLASMA": "Math: Balance chips and mult equally (50×50=2500)",
    "ERRATIC": "Random: Check deck composition Ante 1 - adapt to dominant suits/ranks",
}


class PlannerContextBuilder:
    """Builds the planner context string from game state and phase directive."""

    def __init__(self, session_memory: typing.Deque | None = None):
        self.session_memory = session_memory or []

    def build(
        self,
        tick_context: "TickContext",
        phase_directive: PhaseDirective,
        run_profile: dict,
        guided_options_str: str = "",
        algorithm_discard_hint: str = "",
        state_json: str = "",
        session_memory: typing.Deque | None = None,
        blocked_actions: dict | None = None,
    ) -> str:
        """Build complete planner context string."""
        # Lazy imports to avoid circular dependency
        from .session import _get_boss_debuff_summary

        raw_state = tick_context.raw_state

        # Build boss constraint block
        boss_constraint, _, _ = _get_boss_debuff_summary(raw_state)
        boss_block = self._build_boss_block(boss_constraint)

        # Build run profile block
        run_block = self._build_run_block(run_profile)

        # Build deck selection block (only in MENU state)
        current_state = (raw_state.get("state", "") or "").upper()
        deck_block = (
            self._build_deck_selection_block() if "MENU" in current_state else ""
        )

        # Build desperate mode block (lethal myopia)
        desperate_block = self._build_desperate_mode(tick_context)

        # Build memory string
        memory_str = self._build_memory_string(session_memory or self.session_memory)

        # Handle None phase_directive (e.g., game over state)
        if phase_directive is not None:
            phase_guidance = phase_directive.guidance
            allowed_actions = ", ".join(phase_directive.full_allowed_actions)
        else:
            phase_guidance = "Unknown game state"
            allowed_actions = "unknown"

        # Reinforce state-specific constraints so planner does not pick invalid actions.
        state_constraints = ""
        current_state_lower = (raw_state.get("state", "") or "").lower()

        if "pack" in current_state_lower or "booster" in current_state_lower:
            state_constraints = (
                "STATE CONSTRAINTS:\n"
                "- Booster/pack selection is active. Only choose_pack or skip_pack are valid.\n"
                "- Do not use consumables, sell, or rearrange actions in this state.\n\n"
            )
        elif "round_eval" in current_state_lower:
            # Round evaluation: tally screen after winning a blind.
            # The ONLY valid action is cash_out. The shop is NOT open yet.
            consumable_cards_re = _get_state_cards(raw_state, "consumeables")
            _cons_note = (
                f" You have {len(consumable_cards_re)} consumable(s) available."
                if consumable_cards_re
                else " You have 0 consumables — do NOT output use_consumable."
            )
            state_constraints = (
                "STATE CONSTRAINTS:\n"
                "- ROUND EVALUATION: You just beat the blind. The shop is NOT open yet.\n"
                "- The ONLY correct action is cash_out to collect your money.\n"
                "- Do NOT output buy_shop, buy_pack, reroll, or any shop action.\n"
                "- Do NOT output use_consumable here — the game API blocks it in this state.\n"
                + f"- Consumables: {_cons_note}\n\n"
            )
        elif "blind" in current_state_lower:
            # Blind selection: player is choosing small/big/boss blind.
            state_constraints = (
                "STATE CONSTRAINTS:\n"
                "- BLIND SELECTION: You are choosing which blind to play.\n"
                "- The shop is CLOSED. Do NOT output buy_shop, buy_pack, reroll, or any shop action.\n"
                "- The ONLY valid action is select (to enter the blind).\n"
                "- Ignore any shop item listings visible in game state — they are stale/closed.\n\n"
            )
        elif "shop" in current_state_lower:
            shop_cards = _get_state_cards(raw_state, "shop")
            pack_cards = _get_state_cards(raw_state, "packs")
            voucher_cards = _get_state_cards(raw_state, "vouchers")
            consumable_cards = _get_state_cards(raw_state, "consumeables")
            money = _safe_int(raw_state.get("money"), 0)
            round_info = raw_state.get("round", {})
            reroll_cost = (
                _safe_int(round_info.get("reroll_cost"), 0)
                if isinstance(round_info, dict)
                else 0
            )
            can_reroll = reroll_cost > 0 and money >= reroll_cost

            # Compute joker-target progress for the constraint hint
            _ante_pb = _safe_int(raw_state.get("ante_num"), 1)
            _surv_cnt = _count_survival_jokers(raw_state)
            _eff_res = EARLY_GAME_INTEREST_RESERVE
            _joker_container = raw_state.get("jokers") or {}
            _joker_total = _safe_int(
                _joker_container.get("size")
                if isinstance(_joker_container, dict)
                else 0,
                5,
            )
            _joker_used = len(
                _joker_container.get("cards", [])
                if isinstance(_joker_container, dict)
                else []
            )
            _joker_free = max(0, _joker_total - _joker_used)
            _deck_name = (
                str(run_profile.get("deck") or "")
                if isinstance(run_profile, dict)
                else ""
            )

            _shop_cache = None
            ensure_shop_cache = getattr(tick_context, "ensure_shop_cache", None)
            if callable(ensure_shop_cache):
                try:
                    candidate_cache = ensure_shop_cache(_deck_name)
                    required_list_fields = (
                        "shop_cards",
                        "shop_summaries",
                        "pack_cards",
                        "voucher_cards",
                    )
                    if candidate_cache is not None and all(
                        isinstance(getattr(candidate_cache, field, None), list)
                        for field in required_list_fields
                    ):
                        _shop_cache = candidate_cache
                except Exception:
                    _shop_cache = None

            if _shop_cache is not None:
                shop_cards = _shop_cache.shop_cards
                pack_cards = _shop_cache.pack_cards
                voucher_cards = _shop_cache.voucher_cards
                _shop_summaries = list(_shop_cache.shop_summaries)
            else:
                _shop_summaries = [
                    _summarize_shop_item(card, raw_state, _deck_name)
                    for card in shop_cards
                ]

            _all_low = shop_cards and all(
                _extract_advisor_core(str(s.get("advisor") or ""))
                in ("BAD SYNERGY", "LOW VALUE", "SITUATIONAL", "")
                for s in _shop_summaries
            )
            _has_bad_synergy = any(
                _extract_advisor_core(str(s.get("advisor") or "")) == "BAD SYNERGY"
                for s in _shop_summaries
            )
            _has_early_buffoon = (_ante_pb <= EARLY_GAME_ANTE_LIMIT) and any(
                _is_buffoon_pack(card) for card in pack_cards
            )
            _reroll_hint = (
                " REROLL: all shop items appear low-value and you have surplus > reroll_cost."
                if (_all_low and reroll_cost > 0 and (money - _eff_res) >= reroll_cost)
                else ""
            )
            state_constraints = (
                "STATE CONSTRAINTS:\n"
                "- In shop, only non-targeted consumables are valid to use.\n"
                "- Do not use targeted consumables that require selecting hand cards.\n\n"
                "- RULE 1: Survive the next blind.\n"
                "- RULE 2: Maintain $25+ interest economy when possible.\n"
                "- Never sacrifice Rule 1 just to satisfy Rule 2.\n"
                "- Trust backend tags: [SAFE TO BUY], [CRITICAL UPGRADE], [COSTS INTEREST], and [SAFE TO SELL].\n"
                "- If joker slots are full and you must buy a [CRITICAL UPGRADE], sell only a joker tagged [SAFE TO SELL].\n"
                f"- Joker slots: {_joker_used}/{_joker_total} used ({_joker_free} FREE). "
                f"Scoring Jokers: {_surv_cnt}/{EARLY_GAME_JOKER_TARGET} target. "
                f"Effective reserve: ${_eff_res}.\n"
                "- Spending order with surplus: (1) vouchers, (2) packs, (3) tarot/planet support, (4) strong joker upgrades, (5) reroll only if no good non-rerollables remain.\n"
                f"- Consumable exception: you may spend down to ${CONSUMABLE_EXCEPTION_FLOOR} for highly impactful Tarot/Planet cards.\n"
                f"- Availability: shop_cards={len(shop_cards)}, packs={len(pack_cards)}, "
                f"vouchers={len(voucher_cards)}, consumables={len(consumable_cards)}, "
                f"money=${money}, reroll_cost=${reroll_cost}.{_reroll_hint}\n"
            )

            if _has_early_buffoon and _surv_cnt == 0:
                state_constraints += "- URGENT: You have no scoring joker yet and Buffoon pack is available. Prioritize buy_pack now.\n"
            if _has_bad_synergy:
                state_constraints += "- BAD SYNERGY items are hard-blocked. Never pick them; skip them entirely and choose a different action.\n"
                state_constraints += "- Unreliable joker effects (chance/random/self-destruct text) are treated as BAD SYNERGY for Checkered runs.\n"

            if not shop_cards:
                state_constraints += "- shop.cards is empty. Do NOT output buy_shop.\n"
            if not pack_cards:
                state_constraints += "- packs.cards is empty. Do NOT output buy_pack.\n"
            if not voucher_cards:
                state_constraints += (
                    "- vouchers.cards is empty. Do NOT output buy_voucher.\n"
                )
            if not can_reroll:
                state_constraints += (
                    "- Reroll is unavailable or unaffordable. Do NOT output reroll.\n"
                )

            # Annotate individual items the bot cannot currently afford
            def _extract_cost(card: dict) -> int | None:
                c = card.get("cost")
                if isinstance(c, int):
                    return c
                if isinstance(c, dict):
                    return c.get("buy") or c.get("sell")
                return None

            for i, card in enumerate(shop_cards, start=1):
                cost = _extract_cost(card)
                if cost is not None and cost > money:
                    state_constraints += (
                        f"- shop item #{i} ({card.get('label', '?')}) costs ${cost} "
                        f"but you only have ${money}. Do NOT output buy_shop [{i}].\n"
                    )
            for i, card in enumerate(pack_cards, start=1):
                cost = _extract_cost(card)
                if cost is not None and cost > money:
                    state_constraints += (
                        f"- pack #{i} ({card.get('label', '?')}) costs ${cost} "
                        f"but you only have ${money}. Do NOT output buy_pack [{i}].\n"
                    )

            if not any(
                [shop_cards, pack_cards, voucher_cards, consumable_cards, can_reroll]
            ):
                state_constraints += (
                    "- No valid shop action besides continue exists. "
                    "Output action='continue' with indices=[].\n"
                )

            state_constraints += "\n"

        elif (
            "play" in current_state_lower
            or "hand" in current_state_lower
            or "selecting_hand" in current_state_lower
        ):
            state_constraints = (
                "STATE CONSTRAINTS:\n"
                "- You are in the play/discard phase. The shop is CLOSED — you cannot buy anything.\n"
                "- Do NOT output buy_shop, buy_pack, buy_voucher, or reroll. "
                "These actions are only valid while in shop state.\n"
                "- Valid actions: play_hand, discard, use_consumable, sell_joker, "
                "sell_consumable, rearrange_joker, rearrange_consumable.\n\n"
            )

        # Build blocked-actions warning block for planner context
        blocked_block = ""
        if blocked_actions:
            lines = [
                f"  - '{act}': {reason}" for act, reason in blocked_actions.items()
            ]
            blocked_block = (
                "BLOCKED ACTIONS (already failed this phase — do NOT retry these):\n"
                + "\n".join(lines)
                + "\n\n"
            )

        # Build final context
        context = (
            f"{deck_block}{boss_block}{run_block}{desperate_block}{state_constraints}"
            f"{blocked_block}"
            f"GAME STATE:\n{state_json}\n\n"
            f"RECENT ACTIONS:\n{memory_str}\n\n"
            f"{guided_options_str}\n"
            f"PHASE GUIDANCE: {phase_guidance}\n"
            f"ALLOWED ACTIONS: {allowed_actions}\n"
            f"{algorithm_discard_hint}"
        ).strip()

        return context

    def _build_boss_block(self, boss_constraint: str) -> str:
        """Build boss constraint block if active."""
        if not boss_constraint:
            return ""
        return f"\n*** BOSS CONSTRAINT ***\n{boss_constraint}\n*** END BOSS CONSTRAINT ***\n"

    def _build_run_block(self, run_profile: dict) -> str:
        """Build run profile block if active."""
        deck = run_profile.get("deck")
        directive = run_profile.get("directive")
        if not deck:
            return ""
        return f"\n[RUN PROFILE] Deck: {deck} — {directive}\n"

    def _build_deck_selection_block(self) -> str:
        """Build strategic deck selection block for menu state."""
        return (
            "\n*** STRATEGIC DECK SELECTION ***\n"
            "Consistency target: CHECKERED only.\n"
            f"- CHECKERED: {DECK_STRATEGIES.get('CHECKERED', 'Flush-focused strategy')}\n"
            "Output action='start_run' with indices=[].\n"
            "*** END STRATEGIC DECK SELECTION ***\n"
        )

    def _build_desperate_mode(self, tick_context: "TickContext") -> str:
        """Build desperate mode block if lethal myopia condition is met."""
        raw_state = tick_context.raw_state

        # Check if we're in play/hand state
        current_state = (raw_state.get("state", "") or "").lower()
        if "play" not in current_state and "hand" not in current_state:
            return ""

        # Get round info
        round_info = raw_state.get("round", {})
        chips_scored = round_info.get("chips", 0) if isinstance(round_info, dict) else 0
        hands_left = (
            round_info.get("hands_left", 0) if isinstance(round_info, dict) else 0
        )
        discards_left = (
            round_info.get("discards_left", 0) if isinstance(round_info, dict) else 0
        )

        # Get required score from active blind
        score_required = 0
        blinds_data = raw_state.get("blinds", {})
        if isinstance(blinds_data, dict):
            for b_name, b_val in blinds_data.items():
                if isinstance(b_val, dict) and b_val.get("status", "").lower() in (
                    "active",
                    "current",
                ):
                    score_required = b_val.get("score", 0)
                    break

        # Calculate score needed
        score_needed = max(0, score_required - chips_scored)
        if score_needed <= 0:
            return ""

        # Get current best hand score
        hand_cards = _get_state_cards(raw_state, "hand")
        if not hand_cards:
            return ""

        best_play_data = BalatroAlgorithm.find_best_hand(hand_cards)
        options = best_play_data.get("options", [])
        current_best_score = options[0].get("score", 0) if options else 0

        # Strong hands that should ALWAYS be played — never discarded, even under score pressure.
        # Flush is the threshold: anything at or above flush tier locks in immediately.
        STRONG_HAND_TYPES = {
            "Flush",
            "Full House",
            "Four of a Kind",
            "Five of a Kind",
            "Flush Five",
            "Straight Flush",
            "Royal Flush",
        }

        current_best_hand_name = options[0].get("hand_name", "") if options else ""

        # If we already hold a strong hand, suppress desperate mode entirely.
        # Tell the planner to commit to it rather than chase something better.
        if current_best_hand_name in STRONG_HAND_TYPES:
            return (
                "\n*** PLAY NOW ***\n"
                f"You already have a {current_best_hand_name}. "
                "This is a strong hand — play it immediately. Do NOT discard.\n"
                "*** END PLAY NOW ***\n"
            )

        # Check desperate condition only for weak hands (below flush tier)
        if discards_left > 0 and score_needed > 0 and hands_left > 0:
            if (current_best_score * hands_left) < score_needed:
                return (
                    "\n*** DESPERATE MODE ***\n"
                    "Your current best hands CANNOT reach the required blind score. "
                    "You MUST use your discards to dig for better cards. Do NOT play "
                    "a weak hand right now, you will lose!\n*** END DESPERATE MODE ***\n"
                )

        return ""

    def _build_memory_string(self, memory: typing.Deque) -> str:
        """Convert memory deque to string."""
        if not memory:
            return "None"
        return "\n".join(f"{i + 1}. {mem}" for i, mem in enumerate(memory))
