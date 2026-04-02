import collections
import copy
import hashlib
import json
import ollama
import random
import threading
import time
import re
from dataclasses import dataclass
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ConfigDict,
    field_validator,
    model_validator,
)
from core.logger import log_system
from skills.balatro_client import GameController
from skills.balatro_bot.modules.algorithms import BalatroAlgorithm

# New modular components
from .router import PhaseRouter, PhaseDirective
from .prompt_builder import PlannerContextBuilder


@dataclass
class TickContext:
    raw_state: dict[str, Any]
    validated_state: Any | None = None

    def update_raw_state(self, new_state: dict[str, Any]) -> None:
        """Update raw_state with fresh data after state refresh."""
        self.raw_state = copy.deepcopy(new_state)

    @staticmethod
    def create(
        raw_state: dict[str, Any], validated_state: Any | None = None
    ) -> "TickContext":
        return TickContext(
            raw_state=copy.deepcopy(raw_state),
            validated_state=copy.deepcopy(validated_state)
            if validated_state is not None
            else None,
        )


@dataclass
class ActionResult:
    succeeded: bool
    error: str | None = None
    persona_reasoning: str = ""


_KNOWN_PLANNER_ACTIONS = {
    "buy_pack",
    "buy_shop",
    "buy_voucher",
    "cash",
    "cash_out",
    "cashout",
    "choose_pack",
    "continue",
    "discard",
    "play_hand",
    "rearrange_consumable",
    "rearrange_joker",
    "reroll",
    "select",
    "sell_consumable",
    "sell_joker",
    "skip",
    "skip_blind",
    "skip_pack",
    "start_run",
    "use_consumable",
}

# Local model reference — mirrors iris_agent.py LLM_MODEL
_LLM_MODEL = "qwen2.5:latest"

# ---------------------------------------------------------------------------
# Planner — Silent JSON Action Calculator
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are a silent Balatro game calculator. Your only job is to output a single JSON object.

OUTPUT FORMAT (strict — no other text, no markdown):
{
  "action": "<action_name>",
  "indices": [<1-based integers>],
  "deck": "<deck_name_only_for_start_run>",
  "reasoning": "<1-2 sentence internal reasoning>"
}

MANDATORY ARGUMENT RULES:
- The actions 'buy_shop', 'buy_pack', 'choose_pack', and 'use_consumable' MUST NOT have empty indices.
- You must provide at least one 1-based integer in the 'indices' array.
- INCORRECT: {"action": "buy_shop", "indices": []}
- CORRECT: {"action": "buy_shop", "indices": [1]}
- If you intended to skip, use the "continue" action. For 'skip', 'continue', 'reroll' actions, use empty array [].
- The actions 'rearrange_joker' and 'rearrange_consumable' require EXACTLY 2 indices: [from, to].
- INCORRECT: {"action": "rearrange_joker", "indices": []}
- CORRECT: {"action": "rearrange_joker", "indices": [1, 2]}
- Do NOT call rearrange_joker or rearrange_consumable unless you have a specific reason to reorder items.
- If a consumable requires selecting cards in your hand (e.g. Tarots), the FIRST index is the consumable's 1-based index, followed by the 1-based indices of the target hand cards. Example: {"action": "use_consumable", "indices": [1, 3, 5]}

RULES:
- "action" must be exactly one of the allowed actions listed in the prompt.
- "indices" contains 1-based card or item positions. Use empty array [] if not needed.
- "deck" is ONLY populated for the start_run action. Leave as empty string "" otherwise.
- "reasoning" is brief internal logic. Keep it short and factual.
- Output ONLY the raw JSON object. Nothing else."""

FILLER_PHRASES = [
    "Hmm, let me think about this hand...",
    "Okay, reading the board.",
    "Let's see what we're working with.",
    "Calculating the best play here.",
    "Checking the shop options.",
    "Right, what do we have...",
    "Give me a sec to evaluate this.",
    "Okay, I see the situation.",
    "Let me check our options real quick.",
    "Reading the state...",
]

# Allowed decks for menu start (limited to CHECKERED and ABANDONED for deck selection)
_DECK_WHITELIST = {
    "CHECKERED",
    "ABANDONED",
}

# Run profile: populated when a new run starts, cleared on game over.
# Holds { "deck": str, "directive": str } for the duration of the run.
_run_profile: dict = {}


class PlannerOutput(BaseModel):
    """Strict planner response contract."""

    model_config = ConfigDict(extra="ignore")

    action: str
    indices: list[int] = Field(default_factory=list)
    deck: str = ""
    reasoning: str = ""

    @model_validator(mode="before")
    @classmethod
    def _handle_singular_index(cls, data: Any) -> Any:
        """Map a solitary `index` field to the expected `indices` list."""
        if isinstance(data, dict) and "index" in data and not data.get("indices"):
            value = data.pop("index")
            data["indices"] = [value] if isinstance(value, int) else (
                value if isinstance(value, list) else []
            )
        return data

    @field_validator("indices", mode="before")
    @classmethod
    def _coerce_indices(cls, value: Any) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        raise ValueError("indices must be a list of integers")

    @model_validator(mode="after")
    def _validate_indices(self) -> "PlannerOutput":
        required_indices = {
            "buy_shop",
            "buy_pack",
            "choose_pack",
            "use_consumable",
        }
        reorder_indices = {"rearrange_joker", "rearrange_consumable"}

        if self.indices:
            invalid = [idx for idx in self.indices if not isinstance(idx, int) or idx < 1]
            if invalid:
                raise ValueError(
                    f"Indices must be 1-based integers; got {invalid} for action '{self.action}'."
                )

        if self.action in required_indices and not self.indices:
            raise ValueError(
                f"Action '{self.action}' requires at least one 1-based index."
            )

        if self.action in reorder_indices and len(self.indices) != 2:
            raise ValueError(
                f"Action '{self.action}' requires exactly two 1-based indices [from, to]."
            )

        return self


# ---------------------------------------------------------------------------
# Boss Blind Intelligence
# ---------------------------------------------------------------------------

# Maps boss blind names to plain-English constraint strings injected into the
# LLM's ephemeral context while playing that blind.  Only the most strategically
# significant blinds are listed; unknown bosses fall back to a generic warning.
BOSS_BLIND_RULES: dict[str, str] = {
    "The Plant": "DEBUFF: All face cards (J, Q, K) score 0 chips. Do NOT play hands that rely on face cards.",
    "The Needle": "DEBUFF: You may only play 1 hand total this round. Play your absolute single best hand immediately.",
    "The Psychic": "DEBUFF: You MUST play exactly 5 cards every hand. Do not play fewer than 5 cards.",
    "The Goad": "DEBUFF: All Spade cards score 0 chips. Avoid Spade-heavy hands.",
    "The Window": "DEBUFF: All Diamond cards score 0 chips. Avoid Diamond-heavy hands.",
    "The Serpent": "DEBUFF: After every hand or discard, your hand is fully redrawn. Discard aggressively for a better hand.",
    "The Ox": "DEBUFF: Playing your most-played hand type this ante costs all your money. Avoid your most-played hand type.",
    "The Eye": "DEBUFF: Each hand type can only be played ONCE this round. Vary hand types with each play.",
    "The Mouth": "DEBUFF: Only one specific hand type may be played this round. Identify the allowed type and play ONLY that.",
    "The Fish": "DEBUFF: Cards are drawn face-down. Account for uncertainty; favour high-probability plays.",
    "The Pillar": "DEBUFF: Cards already played this ante are debuffed. Prefer fresh cards from draws.",
    "The Club": "DEBUFF: All Club cards score 0 chips. Avoid Club-heavy hands.",
    "The Mark": "DEBUFF: All face cards are drawn face-down. You cannot see J, Q, K values.",
    "Cerulean Bell": "DEBUFF: One random card in your hand is forced selected. Plan your hand around it being locked.",
    "Amber Acorn": "DEBUFF: Your Joker abilities are shuffled each hand. Do not rely on any specific Joker this round.",
    "Verdant Leaf": "DEBUFF: All cards score only 1 chip until you SELL a Joker. Sell your weakest Joker first.",
    "Violet Vessel": "DEBUFF: Required score is 6x the normal amount. You must scale aggressively.",
    "Crimson Heart": "DEBUFF: One random Joker is debuffed each hand. Joker contributions are unreliable this round.",
    "Black Wing": "DEBUFF: All played 8s score 0 chips. Avoid hands built around Eights.",
    "The Water": "DEBUFF: You start this round with 0 discards. Do NOT plan to discard — conserve and play your best available hand.",
}

# Maps boss blind names to which card ranks and suits are debuffed.
# Used to filter the algorithm's card evaluator, not just the LLM prompt.
BOSS_DEBUFF_FILTERS: dict[str, dict] = {
    "The Plant": {"ranks": ["J", "Q", "K"]},
    "The Goad": {"suits": ["Spades"]},
    "The Window": {"suits": ["Diamonds"]},
    "The Club": {"suits": ["Clubs"]},
    "Black Wing": {"ranks": ["8"]},
}

# ---------------------------------------------------------------------------
# Macro-Strategy / Run Profile
# ---------------------------------------------------------------------------

DECK_PROFILES: dict[str, str] = {
    "RED": "+1 discard per round. Use the extra discards to aggressively hunt for high-tier hands like Full Houses or Flushes.",
    "BLUE": "+1 hand per round. Provides a safety net for scoring and a consistent +$1 reward at the end of every round.",
    "YELLOW": "Start with an extra $10. Use this to hit the $25 interest cap as fast as possible to snowball your economy.",
    "GREEN": "No interest earned, but you get $2 per remaining Hand and $1 per remaining Discard. Finish rounds quickly to maximize payout.",
    "BLACK": "+1 Joker slot but -1 Hand per round. Focus on high-impact Jokers early; the reduced hand count makes the early game very fragile.",
    "MAGIC": "Start with Crystal Ball (+1 consumable slot) and 2 copies of The Fool. Use The Fool to duplicate high-value Tarot cards like Hermit or Temperance.",
    "NEBULA": "Start with Telescope voucher but -1 consumable slot. Planet packs will always show your most played hand. Focus on scaling one specific hand type.",
    "GHOST": "Spectral cards appear in the shop and you start with a Hex card. High risk, high reward: use Hex to give a key Joker x1.5 Mult early.",
    "ABANDONED": "No face cards (J, Q, K) in the deck. Straights and low-rank synergies easier. PRIORITY: Buy 'Ride the Bus' Joker when offered - scales +1 Mult per hand played.",
    "CHECKERED": "Deck is 26 Spades + 26 Hearts only. Focus on FLUSH hands. PRIORITY: Buy Jupiter Planet (scales Flush), suit-specific Jokers (Heart/Spade effects like Bloodstone, Castle, Arrowhead).",
    "ZODIAC": "Start with Tarot Merchant, Planet Merchant, and Overstock. The shop is your best friend; keep money ready for frequent Tarot and Planet appearances.",
    "PAINTED": "+2 hand size but -1 Joker slot. You have 4 slots total. Use the large hand size to build difficult hands like Straights or Five-of-a-Kind.",
    "ANAGLYPH": "Boss Blinds grant Double Tags. Skip intelligently later in the run to stack multiple high-value rewards like Mega-Packs or Negative Jokers.",
    "PLASMA": "Balance Chips and Mult when scoring (e.g., 50 Chips and 50 Mult becomes 2500). Focus on stacking whichever stat is currently lower to maximize the product.",
    "ERRATIC": "Randomized ranks and suits. Check your deck composition immediately in Ante 1 to see which ranks or suits are naturally dominant.",
}

EARLY_GAME_INTEREST_RESERVE = 25
EARLY_GAME_ANTE_LIMIT = 2


def _state_hash(state_obj: Any) -> str:
    """Return an MD5 hex digest of a serialisable state object for deep change detection."""
    if hasattr(state_obj, "model_dump"):
        serialisable = state_obj.model_dump()
    else:
        serialisable = state_obj

    return hashlib.md5(
        json.dumps(serialisable, sort_keys=True, default=str).encode()
    ).hexdigest()


def _get_boss_debuff_summary(raw_state: dict) -> tuple[str, list[str], list[str]]:
    """Inspect the current game state for a known boss blind and return its constraint.

    Returns:
        (constraint_str, debuffed_ranks, debuffed_suits)
        - constraint_str: Human-readable boss rule to inject into LLM context.
                          Empty string if not a boss blind or boss is unknown.
        - debuffed_ranks: List of rank strings (e.g. ['J', 'Q', 'K']) that score 0.
        - debuffed_suits: List of suit strings (e.g. ['Spades']) that score 0.
    """
    blinds = raw_state.get("blinds") or {}
    if not isinstance(blinds, dict):
        return "", [], []

    boss = blinds.get("boss") or {}
    if not isinstance(boss, dict):
        return "", [], []

    boss_name: str = boss.get("name") or ""
    if not boss_name:
        return "", [], []

    # Only apply boss debuffs when the boss is actually active
    boss_status = str(boss.get("status", "")).lower()
    if boss_status not in ("active", "current", "playing"):
        return "", [], []

    # Also try matching partial name in case the payload has truncated names
    matched_boss_key = ""
    constraint = None
    for known_name, rule in BOSS_BLIND_RULES.items():
        if (
            known_name.lower() in boss_name.lower()
            or boss_name.lower() in known_name.lower()
        ):
            constraint = rule
            matched_boss_key = known_name
            break

    if not constraint:
        # Fall back to the effect string from the API if available
        effect_str = (boss.get("effect") or "").strip()
        if effect_str:
            constraint = f"BOSS RULE: {effect_str}"
        else:
            constraint = f"BOSS BLIND ACTIVE: '{boss_name}'. Play cautiously and adapt to any hidden debuffs."

    # Retrieve algorithmic debuff filters for this boss using matched key
    debuff_filter = BOSS_DEBUFF_FILTERS.get(matched_boss_key, {})
    debuffed_ranks: list[str] = debuff_filter.get("ranks", [])
    debuffed_suits: list[str] = debuff_filter.get("suits", [])

    return constraint, debuffed_ranks, debuffed_suits


def _build_run_profile(deck_name: str) -> dict:
    """Create a run profile dict for the given deck.

    Args:
        deck_name: Uppercase deck name (e.g. 'CHECKERED').

    Returns:
        Dict with keys 'deck' and 'directive'.
    """
    directive = DECK_PROFILES.get(
        deck_name.upper(), "Unknown deck selected. Play standard balanced strategy."
    )
    return {"deck": deck_name.upper(), "directive": directive}


def _safe_int(value, default: int = 0) -> int:
    """Best-effort integer coercion for loose RPC payloads."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_state_cards(raw_state: dict, container_name: str) -> list[dict]:
    """Safely read a card-array container from raw_state."""
    container = raw_state.get(container_name) or {}
    if not isinstance(container, dict):
        return []

    cards = container.get("cards", [])
    if not isinstance(cards, list):
        return []

    return [card for card in cards if isinstance(card, dict)]


def _format_card_short(card: dict) -> str:
    """Format a card as rank+suit (e.g., A♠, 9♦) for logging."""
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


def _get_hand_stats(raw_state: dict) -> dict[str, int]:
    """Collect lightweight hand stats used by shop heuristics."""
    current_jokers = _get_state_cards(raw_state, "jokers")
    hand_cards = _get_state_cards(raw_state, "hand")
    round_info = raw_state.get("round") or {}
    total_chips = round_info.get("chips", 0) if isinstance(round_info, dict) else 0
    return {
        "total_chips": _safe_int(total_chips, 0),
        "joker_count": len(current_jokers),
        "hand_count": len(hand_cards),
    }


def _summarize_shop_item(
    card: dict | None, raw_state: dict, deck_name: str = ""
) -> dict[str, str | int]:
    """Return a human-readable summary for logging and persona reactions."""
    if not isinstance(card, dict):
        return {
            "label": "Unknown item",
            "cost": 0,
            "effect": "",
            "advisor": "",
            "impact_pct": 0.0,
            "replace_idx": "",
        }

    summary = {
        "label": str(card.get("label") or card.get("key") or "Unknown item"),
        "cost": card.get("cost"),  # None if API doesn't expose cost in gamestate
        "effect": str((card.get("value") or {}).get("effect") or ""),
        "advisor": "",
        "impact_pct": 0.0,
        "replace_idx": "",
    }

    current_jokers = _get_state_cards(raw_state, "jokers")
    hand_cards = _get_state_cards(raw_state, "hand")

    if BalatroAlgorithm.is_probable_joker(card):
        try:
            impact = BalatroAlgorithm.evaluate_shop_joker_impact(
                card,
                current_jokers,
                hand_cards,
                deck_name=deck_name,
                deck_state=raw_state,
            )
            summary["impact_pct"] = float(impact.get("delta_pct", 0.0))
            replace_idx = impact.get("replacement_index")
            if replace_idx is not None:
                summary["replace_idx"] = str(replace_idx)

            summary["advisor"] = BalatroAlgorithm.score_joker_value(
                card,
                current_jokers,
                _get_hand_stats(raw_state),
                deck_name,
                hand_cards=hand_cards,
                deck_state=raw_state,
            )
        except Exception:
            summary["advisor"] = ""
            summary["impact_pct"] = 0.0
    elif str(card.get("key") or "").lower().startswith("v_"):
        try:
            summary["advisor"] = BalatroAlgorithm.score_voucher_value(
                card,
                _safe_int(raw_state.get("money"), 0),
                _safe_int(raw_state.get("ante_num"), 1),
            )
        except Exception:
            summary["advisor"] = ""

    return summary


def _extract_cost_value(cost: Any) -> int | None:
    """Extract numeric cost from dict or int format."""
    if cost is None:
        return None
    if isinstance(cost, int):
        return cost
    if isinstance(cost, dict):
        return cost.get("buy") or cost.get("sell")
    return None


def _format_shop_item_summary(summary: dict[str, str | int]) -> str:
    """Format a concise item description for terminal logs."""
    label = str(summary.get("label") or "Unknown item")
    cost = summary.get("cost")
    advisor = str(summary.get("advisor") or "").strip()
    impact_pct = summary.get("impact_pct", 0.0)
    replace_idx = str(summary.get("replace_idx") or "").strip()

    if cost is None:
        cost_str = "cost=unknown"
    elif isinstance(cost, dict):
        cost_str = f"cost=${cost.get('buy', cost.get('sell', '?'))}"
    else:
        cost_str = f"cost=${cost}"
    detail_parts = [cost_str]
    if advisor:
        detail_parts.append(f"advisor={advisor}")
    try:
        impact_value = float(impact_pct)
    except (TypeError, ValueError):
        impact_value = 0.0
    if abs(impact_value) > 0.0:
        detail_parts.append(f"impact={impact_value:+.1f}%")
    if replace_idx:
        detail_parts.append(f"replace=#{replace_idx}")

    return f"{label} ({', '.join(detail_parts)})"


def format_cost_for_display(cost: Any) -> str:
    """Format cost value for persona messages (e.g., '5' or '?')."""
    numeric_cost = _extract_cost_value(cost)
    return str(numeric_cost) if numeric_cost is not None else "?"


def _get_shop_card_by_index(raw_state: dict, card_idx: int) -> dict | None:
    """Resolve a 1-based shop-card index into the matching raw card dict."""
    shop_cards = _get_state_cards(raw_state, "shop")
    zero_idx = card_idx - 1
    if 0 <= zero_idx < len(shop_cards):
        return shop_cards[zero_idx]
    return None


def _has_survival_joker(raw_state: dict) -> bool:
    """True when the owned Joker lineup already has direct Chips/Mult scaling."""
    return any(
        BalatroAlgorithm.joker_has_scoring_effect(joker)
        for joker in _get_state_cards(raw_state, "jokers")
    )


def _early_game_buy_block_reason(
    raw_state: dict, shop_card: dict, deck_name: str = ""
) -> str | None:
    """Block risky early-game buys once a survival Joker has been secured."""
    ante = _safe_int(raw_state.get("ante_num"), 1)
    if ante > EARLY_GAME_ANTE_LIMIT:
        return None

    money = _safe_int(raw_state.get("money"), 0)
    cost = _extract_cost_value(shop_card.get("cost"))
    if cost is None or cost <= 0 or (money - cost) >= EARLY_GAME_INTEREST_RESERVE:
        return None

    # High-synergy bypass: allow buying highly-rated items even if it drops below reserve,
    # as long as we keep at least $15 for minimum survival
    shop_summary = _summarize_shop_item(shop_card, raw_state, deck_name)
    impact_pct = float(shop_summary.get("impact_pct") or 0.0)
    if (
        (shop_summary.get("advisor") == "HIGH SYNERGY" or impact_pct >= 20.0)
        and cost is not None
        and (money - cost) >= 15
    ):
        return None

    has_survival = _has_survival_joker(raw_state)
    shop_card_is_survival = BalatroAlgorithm.is_probable_joker(
        shop_card
    ) and BalatroAlgorithm.joker_has_scoring_effect(shop_card)
    label = str(shop_card.get("label") or shop_card.get("key") or "Unknown item")

    # Already below reserve with no survival joker — enforcing the reserve is pointless,
    # let the bot spend whatever it has on anything it can afford.
    if not has_survival and money < EARLY_GAME_INTEREST_RESERVE:
        return None

    if not has_survival and shop_card_is_survival:
        return None

    if not has_survival:
        return (
            f"Early-game reserve: only a Chips/Mult/xMult Joker may drop below "
            f"${EARLY_GAME_INTEREST_RESERVE}. {label} costs ${cost if cost is not None else '?'}, money=${money}."
        )

    return (
        f"Early-game reserve: scoring Joker already secured, so keep "
        f"${EARLY_GAME_INTEREST_RESERVE} banked. {label} costs ${cost if cost is not None else '?'}, "
        f"money=${money}; only spend excess above the reserve on build buys."
    )


def _build_shop_strategy_block(
    raw_state: dict,
    hand_cards: list[dict],
    deck_name: str = "",
    debuffed_ranks: list[str] | None = None,
    debuffed_suits: list[str] | None = None,
    allows_four_card_hands: bool = False,
    allows_gaps: bool = False,
) -> str:
    """Build a compact strategy block for shop turns with joker replacement math."""
    state_name = str(raw_state.get("state") or "").lower()
    if "shop" not in state_name:
        return ""

    money = _safe_int(raw_state.get("money"), 0)
    ante = _safe_int(raw_state.get("ante_num"), 1)
    reserve = EARLY_GAME_INTEREST_RESERVE if ante <= EARLY_GAME_ANTE_LIMIT else 25
    surplus = max(0, money - reserve)
    reroll_cost = _safe_int((raw_state.get("round") or {}).get("reroll_cost"), 0)

    current_jokers = _get_state_cards(raw_state, "jokers")
    shop_cards = _get_state_cards(raw_state, "shop")
    vouchers = _get_state_cards(raw_state, "vouchers")
    packs = _get_state_cards(raw_state, "packs")

    safe_sells = BalatroAlgorithm.identify_safe_sell_jokers(
        current_jokers,
        hand_cards,
        deck_name=deck_name,
        tolerance_pct=2.5,
        debuffed_ranks=debuffed_ranks,
        debuffed_suits=debuffed_suits,
        allows_four_card_hands=allows_four_card_hands,
        allows_gaps=allows_gaps,
    )
    safe_by_index = {int(row.get("index", 0)): row for row in safe_sells}

    baseline = BalatroAlgorithm.estimate_best_score(
        hand_cards,
        current_jokers=current_jokers,
        deck_name=deck_name,
        debuffed_ranks=debuffed_ranks,
        debuffed_suits=debuffed_suits,
        allows_four_card_hands=allows_four_card_hands,
        allows_gaps=allows_gaps,
    )

    lines: list[str] = [
        "*** SHOP STRATEGY ***",
        f"- Money=${money}; reserve=${reserve}; surplus=${surplus}; reroll_cost=${reroll_cost}",
        f"- Theoretical best hand score with current jokers: {baseline}",
    ]

    if current_jokers:
        lines.append("- Current jokers (sell risk):")
        for idx, joker in enumerate(current_jokers, start=1):
            name = str(joker.get("label") or joker.get("key") or f"Joker {idx}")
            safe_info = safe_by_index.get(idx)
            if safe_info:
                loss = float(safe_info.get("loss_pct", 0.0))
                lines.append(f"  [{idx}] {name} -> SAFE_TO_SELL ({loss:.1f}% score loss)")
            else:
                lines.append(f"  [{idx}] {name} -> KEEP")
    else:
        lines.append("- Current jokers: none")

    if shop_cards:
        lines.append("- Shop cards:")
        for idx, card in enumerate(shop_cards, start=1):
            summary = _summarize_shop_item(card, raw_state, deck_name)
            item_line = f"  [{idx}] {_format_shop_item_summary(summary)}"
            if BalatroAlgorithm.is_probable_joker(card):
                replace_idx = summary.get("replace_idx")
                impact_pct = float(summary.get("impact_pct") or 0.0)
                if replace_idx:
                    try:
                        replace_idx_int = int(str(replace_idx))
                        if replace_idx_int in safe_by_index:
                            item_line += f" -> RECOMMENDED: sell_joker [{replace_idx_int}] then buy_shop [{idx}]"
                        else:
                            item_line += f" -> slot full: only replace #{replace_idx_int} if forced"
                    except (TypeError, ValueError):
                        pass
                if impact_pct >= 15.0 and not replace_idx:
                    item_line += " -> RECOMMENDED: high-impact buy if affordable"
            lines.append(item_line)
    else:
        lines.append("- Shop cards: empty")

    if vouchers:
        voucher_summary = _summarize_shop_item(vouchers[0], raw_state, deck_name)
        lines.append(f"- Voucher: {_format_shop_item_summary(voucher_summary)}")

    if packs:
        lines.append(f"- Packs available: {len(packs)} (buy_pack if surplus allows)")

    if surplus > 0:
        lines.append(
            "- Priority: spend surplus above reserve on positive-impact joker upgrades, then packs/vouchers, then reroll."
        )
    else:
        lines.append(
            "- Priority: protect reserve; only buy if high-impact upgrade or critical survival value."
        )

    if safe_sells:
        sell_indices = [str(int(row.get("index", 0))) for row in safe_sells[:2] if int(row.get("index", 0)) > 0]
        if sell_indices:
            lines.append(
                f"- If joker slots are full, preferred sell_joker candidates: {', '.join(sell_indices)}"
            )

    lines.append("*** END SHOP STRATEGY ***")
    return "\n".join(lines)


def _log_decision_safe(
    action: str, api_result: bool, error: str | None, raw_state: dict
) -> None:
    """Best-effort telemetry hook for planner decisions."""
    try:
        from skills.balatro_bot.modules.balatro_telemetry import log_decision

        log_decision(action, api_result, error, raw_state)
    except Exception as exc:
        log_system(f"[Balatro] Telemetry log_decision failed: {exc}")


def _call_planner(planner_context: str) -> dict:
    """Call Qwen as a silent JSON planner. Always returns a valid action dict.

    Uses temperature=0.0 and format='json' for deterministic structured output.
    Strips markdown fences if the model wraps output in them.
    Falls back to {"action": "continue", ...} on any parse failure.
    """
    _DEFAULT = PlannerOutput(
        action="continue",
        indices=[],
        deck="",
        reasoning="Fallback: planner output could not be parsed.",
    ).model_dump()
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": planner_context},
    ]
    try:
        response = ollama.chat(
            model=_LLM_MODEL,
            messages=messages,
            stream=False,
            format="json",
            options={"temperature": 0.0},
        )
        raw = (response.get("message", {}).get("content", "") or "").strip()
        # Strip markdown fences if Qwen wraps output (e.g. ```json ... ```)
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        parsed_json = json.loads(clean)

        try:
            parsed = PlannerOutput.model_validate(parsed_json).model_dump()
        except ValidationError as ve:
            log_system(f"[Planner] Validation failed: {ve}. Raw: {raw[:120]}")
            return _DEFAULT

        log_system(
            f"[Planner] action={parsed['action']!r} "
            f"indices={parsed['indices']} "
            f"reasoning={parsed['reasoning'][:80]}"
        )
        return parsed
    except Exception as exc:
        log_system(f"[Planner] Parse failed ({exc}). Using fallback default.")
        return _DEFAULT


class BalatroSession:
    def __init__(self, iris, avatar):
        self.iris = iris
        self.avatar = avatar
        self.controller = GameController()
        self.stop_event = threading.Event()
        self.run_profile = {}
        self.action_memory = collections.deque(maxlen=3)
        self.persona_recent = collections.deque(maxlen=3)
        self.last_state_hash = None
        self._stall_ticks = 0
        self._last_filler_time = 0.0
        self._last_hand_cards: list[dict] = []
        self._last_ante_num: int = 0

        # New modular components (ActionRegistry imported lazily in run_loop to avoid circular import)
        self.phase_router = PhaseRouter()
        self.prompt_builder = PlannerContextBuilder(self.action_memory)

    def _call_persona(self, reasoning: str, succeeded: bool) -> None:
        """Fire Iris's streamer persona reaction after a planner action executes.

        Uses a direct ollama.chat() call to bypass self.iris.xxx's hardcoded 0.35
        temperature, allowing more creative (0.7) VTuber reactions without polluting
        the core agent memory. Streams output to TTS and logs the response to
        prevent repetition on subsequent turns.
        """
        result_word = (
            "succeeded" if succeeded else "FAILED — react with surprise or frustration"
        )

        # Inject recent history to break repetitive loops
        repetition_shield = ""
        if self.persona_recent:
            recent_lines = "\n".join(f"- {line}" for line in self.persona_recent)
            repetition_shield = f"CRITICAL: Do NOT repeat or closely paraphrase these recent lines:\n{recent_lines}\n\n"

        prompt = (
            repetition_shield + f"You just decided: {reasoning} "
            f"The action {result_word}. "
            "React naturally in 1-2 sentences as a VTuber streamer. No action tags."
        )

        messages = [
            {"role": "system", "content": self.iris.mode_data.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        if self.avatar is not None:
            self.avatar.show_thinking()
            self.avatar.set_state("thinking")

        try:
            response_stream = ollama.chat(
                model=_LLM_MODEL,
                messages=messages,
                stream=True,
                options={"temperature": 0.7},
            )
            content, _ = self.iris._speak_streamed(response_stream, avatar=self.avatar)
            if content:
                self.persona_recent.append(content)
        except Exception as e:
            log_system(f"[_call_persona] TTS Stream failed: {e}")

    def _patch_system_prompt_with_profile(self) -> None:
        """Append the current run profile directive to Iris's system prompt in-place.

        Called after switch_mode() so the injected directive persists for the
        entire run without polluting the base SYSTEM_PROMPT constant.
        Must be called while _balatro_lock is NOT held (self.iris.lock is acquired inside).
        """
        directive = self.run_profile.get("directive", "")
        deck = self.run_profile.get("deck", "")
        if not directive:
            return
        with self.iris.lock:
            if self.iris.messages and self.iris.messages[0].get("role") == "system":
                existing = self.iris.messages[0]["content"]
                marker = "\n\n[RUN PROFILE]"
                # Avoid double-injection if already patched
                if marker not in existing:
                    self.iris.messages[0]["content"] = (
                        existing + f"{marker}\nDeck: {deck}\nStrategy: {directive}"
                    )
                    log_system(
                        f"[Balatro] Run Profile injected into system prompt: {deck} — {directive[:60]}..."
                    )

    def run_loop(self):

        log_system("[Balatro] Game loop started.")

        while True:
            try:
                # --- Check stop signal ---
                if self.stop_event.is_set():
                    break
                controller = self.controller

                if controller is None:
                    break

                success = controller.refresh_state()
                if not success:
                    time.sleep(2)
                    continue

                # --- Deep state-change detection via MD5 hash on validated model ---
                state_for_hash = (
                    controller.current_state.model_dump()
                    if controller.current_state is not None
                    else controller.raw_state
                )
                current_hash = _state_hash(state_for_hash)
                if current_hash == self.last_state_hash:
                    self._stall_ticks += 1
                    if self._stall_ticks >= 6:  # ~30s of frozen state
                        log_system(
                            f"[Balatro] SOFTLOCK DETECTED ({self._stall_ticks} stall ticks) — forcing proceed_next."
                        )
                        controller.client.proceed_next()
                        self._stall_ticks = 0
                        self.last_state_hash = None  # force re-eval after escape
                    else:
                        time.sleep(5)
                    continue
                self._stall_ticks = 0
                self.last_state_hash = current_hash

                tick_context = TickContext.create(
                    controller.raw_state,
                    controller.current_state.model_copy(deep=True)
                    if controller.current_state is not None
                    else None,
                )
                raw_state = tick_context.raw_state

                # --- Drain Check: Suppress filler if Persona is still talking ---
                persona_is_talking = False
                if self.iris.vm and (
                    self.iris.vm.is_speaking.is_set()
                    or not self.iris.vm.speech_queue.empty()
                ):
                    persona_is_talking = True

                # --- Evaluate best hand from current hand ---
                best_hand_name = "None"
                card_indices_to_play = []
                hand_cards = []
                guided_options_str = ""

                # Derive state identifier early — needed by boss debuff and shop-scoring branches
                current_state_lower = (raw_state.get("state", "") or "").lower()
                ante = raw_state.get("ante_num", 1)

                if "cards" in raw_state.get("hand", {}):
                    hand_cards = raw_state.get("hand", {}).get("cards", [])
                    reported_count = raw_state.get("hand", {}).get("count")
                    playable_count = reported_count or len(hand_cards)

                    if reported_count and reported_count < len(hand_cards):
                        log_system(
                            f"[Balatro] Hand count mismatch on evaluate: "
                            f"len(cards)={len(hand_cards)}, reported count={reported_count}. "
                            f"Trimming to reported count."
                        )
                        hand_cards = hand_cards[:playable_count]

                # --- Detect newly drawn cards ---
                current_formatted = {
                    _format_card_short(c) for c in hand_cards if isinstance(c, dict)
                }
                last_formatted = {
                    _format_card_short(c)
                    for c in self._last_hand_cards
                    if isinstance(c, dict)
                }
                new_ante = raw_state.get("ante_num", 1)
                if new_ante != self._last_ante_num:
                    self._last_hand_cards = []
                    self._last_ante_num = new_ante
                    log_system(f"[Balatro] --- Ante {new_ante} started ---")
                elif current_formatted != last_formatted and current_formatted:
                    new_cards = [
                        c
                        for c in hand_cards
                        if isinstance(c, dict)
                        and _format_card_short(c) not in last_formatted
                    ]
                    if new_cards:
                        drawn_str = ", ".join(_format_card_short(c) for c in new_cards)
                        log_system(f"[Balatro] Drawn: {drawn_str}")
                    elif not last_formatted:
                        all_str = ", ".join(_format_card_short(c) for c in hand_cards)
                        log_system(f"[Balatro] Hand: {all_str}")
                self._last_hand_cards = list(hand_cards)

                # --- Resolve active boss debuff filters (used by algo AND LLM context) ---
                active_boss_constraint = ""
                active_debuffed_ranks: list[str] = []
                active_debuffed_suits: list[str] = []
                if "playing" in current_state_lower or "hand" in current_state_lower:
                    (
                        active_boss_constraint,
                        active_debuffed_ranks,
                        active_debuffed_suits,
                    ) = _get_boss_debuff_summary(raw_state)
                    if active_boss_constraint:
                        log_system(
                            f"[Balatro] Boss constraint active: {active_boss_constraint[:80]}"
                        )

                # Extract active Jokers to check for game-changing rules
                joker_list = []
                _jokers_state = raw_state.get("jokers", {})
                if isinstance(_jokers_state, dict):
                    joker_list = _jokers_state.get("cards", [])
                    if not isinstance(joker_list, list):
                        joker_list = []

                allows_four_card_hands = False
                allows_gaps = False
                for j in joker_list:
                    if not isinstance(j, dict):
                        continue
                    key = j.get("key", "").lower()
                    if key == "j_four_fingers":
                        allows_four_card_hands = True
                    elif key == "j_shortcut":
                        allows_gaps = True

                options = []
                if hand_cards:
                    deck_name = self.run_profile.get("deck", "") if self.run_profile else ""
                    best_play_data = BalatroAlgorithm.find_best_hand(
                        hand_cards,
                        debuffed_ranks=active_debuffed_ranks
                        if active_debuffed_ranks
                        else None,
                        debuffed_suits=active_debuffed_suits
                        if active_debuffed_suits
                        else None,
                        allows_four_card_hands=allows_four_card_hands,
                        allows_gaps=allows_gaps,
                        current_jokers=joker_list,
                        deck_name=deck_name,
                    )

                    options = best_play_data.get("options", [])
                    if options:
                        # Set fallback to option A for algorithm hints/discard
                        best_hand_name = options[0].get("hand_name", "None")
                        card_indices_to_play = options[0].get("indices", [])

                        guided_options_str = "*** GUIDED PLAY OPTIONS ***\n"
                        for opt in options:
                            guided_options_str += f"Option {opt['label']}: Play {opt['hand_name']} (Indices: {opt['indices']}) - Score: {opt['score']}\n"

                        guided_options_str += "***************************\n"

                # --- Handle pack options ---
                if "pack" in current_state_lower or "booster" in current_state_lower:
                    pack_cards = raw_state.get("pack", {}).get("cards", [])
                    if pack_cards:
                        joker_container = raw_state.get("jokers") or {}
                        current_jokers = (
                            joker_container.get("cards", [])
                            if isinstance(joker_container, dict)
                            else []
                        )
                        round_info = raw_state.get("round") or {}
                        total_chips = (
                            round_info.get("chips", 0)
                            if isinstance(round_info, dict)
                            else 0
                        )
                        hand_stats = {
                            "total_chips": total_chips,
                            "joker_count": len(current_jokers),
                        }

                        pack_options = BalatroAlgorithm.evaluate_pack_options(
                            pack_cards, current_jokers, hand_stats
                        )
                        if not guided_options_str:
                            guided_options_str = "*** GUIDED PACK OPTIONS ***\n"
                        for opt in pack_options:
                            guided_options_str += f"Option {opt['label']}: {opt['reasoning']} (Action: {opt['action']}, Indices: {opt['indices']})\n"
                        guided_options_str += "***************************\n"

                # --- Handle consumables options (global across all states) ---
                consumables = raw_state.get("consumables", {}).get("cards", [])
                if consumables:
                    deck_name = self.run_profile.get("deck", "") if self.run_profile else ""
                    cons_options = BalatroAlgorithm.evaluate_consumable_plays(
                        consumables,
                        hand_cards,
                        deck_name=deck_name,
                    )
                    if cons_options:
                        if not guided_options_str:
                            guided_options_str = "*** GUIDED CONSUMABLES OPTIONS ***\n"
                        for opt in cons_options:
                            guided_options_str += f"Option {opt['label']}: {opt['reasoning']} (Action: {opt['action']}, Indices: {opt['indices']})\n"
                        guided_options_str += "***************************\n"

                # --- Inject shop-specific strategy block with joker replacement math ---
                if "shop" in current_state_lower:
                    deck_name = self.run_profile.get("deck", "") if self.run_profile else ""
                    strategy_block = _build_shop_strategy_block(
                        raw_state,
                        hand_cards,
                        deck_name=deck_name,
                        debuffed_ranks=active_debuffed_ranks,
                        debuffed_suits=active_debuffed_suits,
                        allows_four_card_hands=allows_four_card_hands,
                        allows_gaps=allows_gaps,
                    )
                    if strategy_block:
                        if guided_options_str:
                            guided_options_str += "\n"
                        guided_options_str += strategy_block + "\n"

                # --- Build state JSON ---
                if "shop" in current_state_lower:
                    state_json = controller.get_context_for_llm_with_joker_scores(
                        BalatroAlgorithm.score_joker_value
                    )
                else:
                    state_json = controller.get_context_for_llm()

                # --- Algorithm discard hint ---
                algo_discard_hint = ""
                if hand_cards and card_indices_to_play:
                    discard_suggestion = BalatroAlgorithm.find_best_discard(
                        hand_cards, card_indices_to_play
                    )
                    if discard_suggestion:
                        algo_discard_hint = f"Algorithm discard hint — suggested 1-based indices to discard: {discard_suggestion}"

                # --- Rolling action memory ---
                memory_str = (
                    "\n".join(
                        f"{i + 1}. {mem}" for i, mem in enumerate(self.action_memory)
                    )
                    if self.action_memory
                    else "None"
                )

                # --- NEW: Phase routing using PhaseRouter ---
                phase_directive = self.phase_router.get_directive(tick_context)

                if phase_directive is None:
                    # Game over or unknown state - handled specially below
                    phase_guidance = ""
                    allowed_actions = ""
                else:
                    phase_guidance = phase_directive.guidance
                    allowed_actions = ", ".join(phase_directive.full_allowed_actions)

                # --- Assemble Planner context using PlannerContextBuilder ---
                planner_context = self.prompt_builder.build(
                    tick_context=tick_context,
                    phase_directive=phase_directive,
                    run_profile=self.run_profile,
                    guided_options_str=guided_options_str,
                    algorithm_discard_hint=algo_discard_hint,
                    state_json=state_json,
                    session_memory=self.action_memory,
                )

                # --- Step 2: Fire non-blocking filler TTS (throttled — max once per 12s) ---
                _now = time.time()
                if (
                    self.iris.vm
                    and not persona_is_talking
                    and (_now - self._last_filler_time) > 12.0
                ):
                    self.iris.vm.speak(
                        random.choice(FILLER_PHRASES),
                        avatar=self.avatar,
                        check_watchdog=False,
                    )

                # --- Step 3: Call silent Planner ---
                planner = _call_planner(planner_context)
                action_tag = planner["action"].lower().strip().replace(" ", "_")
                args = planner["indices"]  # 1-based int list from Planner JSON
                raw_args_str = planner.get("deck", "")  # deck name for start_run only

                # Override card_indices_to_play with planner choice for play_hand
                # (algo result stays as fallback if planner returns empty indices)
                if action_tag == "play_hand" and args:
                    card_indices_to_play = args

                log_system(
                    f"[Balatro] Planner: action={action_tag!r} indices={args} deck={raw_args_str!r}"
                )

                # --- Step 4: Re-acquire controller before dispatch ---
                if self.stop_event.is_set():
                    break
                controller = self.controller

                if controller is None:
                    break

                # api_succeeded is set to False by dispatch branches that fail,
                # then read by the Persona call at the end of the tick.
                api_succeeded = True
                decision_error = None
                persona_reasoning = planner.get("reasoning", "")

                log_system(
                    f"[Balatro] Executing action: {action_tag!r} with args: {args!r}, raw_str: {raw_args_str!r}"
                )

                current_state = (controller.raw_state.get("state", "") or "").lower()

                # --- Special case: GAME OVER ---
                if "game_over" in current_state:
                    log_system(
                        "[Balatro] Game Over detected. Logging death and triggering menu."
                    )
                    # --- Feature 4: Telemetry death log ---
                    try:
                        from skills.balatro_bot.modules.balatro_telemetry import (
                            log_death,
                        )

                        log_death(raw_state)
                    except Exception as _tel_err:
                        log_system(f"[Balatro] Telemetry log_death failed: {_tel_err}")
                    # --- Clear run profile for next run ---
                    self.run_profile = {}
                    self.iris.chat(
                        "The run is over! Game over! Say something dramatic, but no tags needed.",
                        save=False,
                        use_tools=False,
                        avatar=self.avatar,
                    )
                    # Try to return to menu - wait for Game Over tally to finish, then call menu
                    log_system("[Balatro] DEBUG: Waiting for Game Over tally...")
                    time.sleep(1.0)  # Let the tally screen finish
                    log_system("[Balatro] DEBUG: Attempting to return to menu...")
                    menu_result = controller.client._call("menu")
                    log_system(f"[Balatro] DEBUG: menu call result: {menu_result}")
                    if not menu_result:
                        # Try alternative command
                        log_system(
                            "[Balatro] DEBUG: Trying alternative command 'main_menu'..."
                        )
                        controller.client._call("main_menu")
                    time.sleep(3)
                    continue

                # --- Phase-aligned guardrail: disallow actions outside the directive ---
                if (
                    phase_directive
                    and action_tag not in phase_directive.full_allowed_actions
                ):
                    decision_error = (
                        f"Action '{action_tag}' is not allowed in phase '{phase_directive.phase_id}'. "
                        f"Allowed actions: {phase_directive.full_allowed_actions}"
                    )
                    log_system(f"[ROUTER] {decision_error}")
                    self.action_memory.append(
                        f"Action: [{action_tag}] -> Error: {decision_error}"
                    )
                    api_succeeded = False
                    _log_decision_safe(action_tag, False, decision_error, raw_state)
                    time.sleep(1)
                    continue

                if action_tag not in _KNOWN_PLANNER_ACTIONS:
                    decision_error = f"Unsupported planner action '{action_tag}' in state '{current_state}'."
                    log_system(f"[Balatro] {decision_error}")
                    self.action_memory.append(
                        f"Action: [{action_tag}] -> Error: {decision_error}"
                    )
                    api_succeeded = False
                    _log_decision_safe(action_tag, False, decision_error, raw_state)
                    time.sleep(1)
                    continue

                # --- NEW: Try ActionRegistry dispatch ---
                from .actions import ActionContext, ActionRegistry

                action_class = ActionRegistry.get(action_tag)
                used_new_path = False
                if action_class is not None:
                    log_system(
                        f"[ROUTER] >>> EXECUTING VIA NEW MODULE: {action_tag} <<<"
                    )
                    used_new_path = True
                    ctx = ActionContext(
                        session=self,
                        tick_context=tick_context,
                        planner_output=planner,
                        controller=controller,
                        state=current_state,
                    )
                    action = action_class()
                    result = action.execute(ctx)
                    api_succeeded = result.succeeded
                    decision_error = result.error
                    persona_reasoning = result.persona_reasoning

                    if result.succeeded:
                        self.action_memory.append(f"Action: [{action_tag}] -> Success")
                        # Tier 3: Force fresh state pull after inventory-changing actions
                        if action_tag in [
                            "buy_shop",
                            "buy_pack",
                            "choose_pack",
                            "use_consumable",
                        ]:
                            self.last_state_hash = None
                            time.sleep(2.0)
                    else:
                        error_feedback = None
                        if (
                            action_tag
                            in [
                                "buy_shop",
                                "buy_pack",
                                "choose_pack",
                                "use_consumable",
                            ]
                            and not args
                        ):
                            error_feedback = (
                                f"SYSTEM ERROR: [{action_tag}] failed - 'indices' was empty. "
                                "You MUST provide a 1-based index."
                            )
                            decision_error = error_feedback
                            log_system(
                                f"[Planner] Negative feedback injected for {action_tag}"
                            )
                        elif decision_error:
                            error_feedback = (
                                f"Action: [{action_tag}] -> Error: {decision_error}"
                            )

                        if error_feedback:
                            self.action_memory.append(error_feedback)

                    time.sleep(1)

                if not used_new_path:
                    decision_error = (
                        f"Action '{action_tag}' is not registered in ActionRegistry. "
                        "The modular architecture must handle this action."
                    )
                    log_system(
                        f"[ROUTER] !!! ERROR: ACTION NOT REGISTERED: {action_tag} !!!"
                    )
                    api_succeeded = False
                    self.action_memory.append(
                        f"Action: [{action_tag}] -> Error: Not registered in modular system"
                    )
                    _log_decision_safe(action_tag, False, decision_error, raw_state)
                    time.sleep(1)
                    continue

                _log_decision_safe(action_tag, api_succeeded, decision_error, raw_state)

                # --- Step 5: Persona reaction async ---
                log_system("[Balatro] Firing persona async thread.")
                threading.Thread(
                    target=self._call_persona,
                    args=(persona_reasoning, api_succeeded),
                    daemon=True,
                ).start()

                # Give planner a brief pause to prevent absolute spam, but don't block on TTS
                time.sleep(1)

            except Exception as e:
                import traceback

                log_system(f"[BalatroSession] Tick Crash Containment: {e}")
                traceback.print_exc()
                time.sleep(2)

        # Teardown
        self.controller = None
        log_system("[Balatro] Game loop exited — teardown complete.")


_active_session: BalatroSession | None = None
_session_lock = threading.Lock()


def start_session(iris, avatar):
    global _active_session
    with _session_lock:
        if _active_session is not None:
            _active_session.stop_event.set()
        _active_session = BalatroSession(iris, avatar)
    log_system("[BalatroSession] Starting new session.")
    _active_session.iris.switch_mode("balatro", avatar=_active_session.avatar)
    threading.Thread(target=_active_session.run_loop, daemon=True).start()


def stop_session():
    global _active_session
    with _session_lock:
        if _active_session is not None:
            _active_session.stop_event.set()
            _active_session = None
    log_system("[BalatroSession] Stop requested.")


def get_active_session() -> BalatroSession | None:
    return _active_session
