import typing
from typing import TYPE_CHECKING
from core.logger import log_system

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
    "ABANDONED": "No face cards - Straights and low-rank synergies easier. PRIORITY: Buy 'Ride the Bus' Joker when offered.",
    "CHECKERED": "Synergy: Flush only (26♠ 26♥) - suit Jokers priority (Heart/Spade). PRIORITY: Jupiter Planet, Bloodstone, Castle, Arrowhead.",
    "ZODIAC": "Merchant: Tarot/Planet/Overstock appear more - save money for shop",
    "PAINTED": "Size: +2 hand size, -1 joker slot (4 total) - big hands priority",
    "ANAGLYPH": "Double Tags: Boss blinds grant 2 tags - skip for mega rewards",
    "PLASMA": "Math: Balance chips and mult equally (50×50=2500)",
    "ERRATIC": "Random: Check deck composition Ante 1 - adapt to dominant suits/ranks",
}


class PlannerContextBuilder:
    """Builds the planner context string from game state and phase directive."""

    def __init__(self, session_memory: typing.Optional[typing.Deque] = None):
        self.session_memory = session_memory or []

    def build(
        self,
        tick_context: "TickContext",
        phase_directive: PhaseDirective,
        run_profile: dict,
        guided_options_str: str = "",
        algorithm_discard_hint: str = "",
        state_json: str = "",
        session_memory: typing.Optional[typing.Deque] = None,
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
        elif "shop" in current_state_lower:
            state_constraints = (
                "STATE CONSTRAINTS:\n"
                "- In shop, only non-targeted consumables are valid to use.\n"
                "- Do not use targeted consumables that require selecting hand cards.\n\n"
                "- Use surplus money above reserve to improve board strength (joker upgrades, packs, vouchers, rerolls).\n"
                "- If joker slots are full, prefer selling low-impact jokers before buying a stronger joker.\n\n"
            )

        # Build final context
        context = (
            f"{deck_block}{boss_block}{run_block}{desperate_block}{state_constraints}"
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
            "Output deck='CHECKERED' in the planner JSON.\n"
            "*** END STRATEGIC DECK SELECTION ***\n"
        )

    def _build_desperate_mode(self, tick_context: "TickContext") -> str:
        """Build desperate mode block if lethal myopia condition is met."""
        from .session import BalatroAlgorithm, _get_state_cards

        raw_state = tick_context.raw_state

        # Check if we're in play/hand state
        current_state = (raw_state.get("state", "") or "").lower()
        if "playing" not in current_state and "hand" not in current_state:
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
