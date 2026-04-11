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
from typing import Any, ClassVar

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
from .router import PhaseRouter
from .prompt_builder import PlannerContextBuilder
from .constants import FILLER_PHRASES
from .shop_analysis import (
    _resolve_explicit_level_maps,
    _summarize_shop_item,
    _build_shop_strategy_block,
)
from .utils import _get_state_cards, _format_card, _format_hand, _safe_int


@dataclass
class ShopTickCache:
    """Derived shop computations reused within one raw-state snapshot."""

    state_hash: str
    deck_name: str
    hand_cards: list[dict]
    jokers: list[dict]
    shop_cards: list[dict]
    pack_cards: list[dict]
    voucher_cards: list[dict]
    shop_summaries: list[dict[str, Any]]
    pack_summaries: list[dict[str, Any]]
    voucher_summaries: list[dict[str, Any]]
    hand_levels_map: dict[str, Any]
    planet_levels_map: dict[str, Any]


@dataclass
class TickContext:
    raw_state: dict[str, Any]
    validated_state: Any | None = None
    shop_cache: ShopTickCache | None = None

    def _invalidate_derived_cache(self) -> None:
        """Clear any derived snapshots tied to the previous raw_state."""
        self.shop_cache = None

    def update_raw_state(self, new_state: dict[str, Any]) -> None:
        """Update raw_state with fresh data after state refresh."""
        self.raw_state = copy.deepcopy(new_state)
        self._invalidate_derived_cache()

    def ensure_shop_cache(self, deck_name: str = "") -> ShopTickCache | None:
        """Build and memoize shop-derived computations for the current state."""
        state_name = str(self.raw_state.get("state") or "").lower()
        if "shop" not in state_name:
            self.shop_cache = None
            return None

        current_hash = _state_hash(self.raw_state)
        normalized_deck = str(deck_name or "")
        if (
            self.shop_cache is not None
            and self.shop_cache.state_hash == current_hash
            and self.shop_cache.deck_name == normalized_deck
        ):
            return self.shop_cache

        hand_cards = _get_state_cards(self.raw_state, "hand")
        jokers = _get_state_cards(self.raw_state, "jokers")
        shop_cards = _get_state_cards(self.raw_state, "shop")
        pack_cards = _get_state_cards(self.raw_state, "packs")
        voucher_cards = _get_state_cards(self.raw_state, "vouchers")
        hand_levels_map, planet_levels_map, _ = _resolve_explicit_level_maps(
            self.raw_state
        )

        self.shop_cache = ShopTickCache(
            state_hash=current_hash,
            deck_name=normalized_deck,
            hand_cards=hand_cards,
            jokers=jokers,
            shop_cards=shop_cards,
            pack_cards=pack_cards,
            voucher_cards=voucher_cards,
            shop_summaries=[
                _summarize_shop_item(card, self.raw_state, normalized_deck)
                for card in shop_cards
            ],
            pack_summaries=[
                _summarize_shop_item(card, self.raw_state, normalized_deck)
                for card in pack_cards
            ],
            voucher_summaries=[
                _summarize_shop_item(card, self.raw_state, normalized_deck)
                for card in voucher_cards
            ],
            hand_levels_map=hand_levels_map,
            planet_levels_map=planet_levels_map,
        )
        return self.shop_cache

    @staticmethod
    def create(
        raw_state: dict[str, Any], validated_state: Any | None = None
    ) -> "TickContext":
        return TickContext(
            raw_state=copy.deepcopy(raw_state),
            validated_state=copy.deepcopy(validated_state)
            if validated_state is not None
            else None,
            shop_cache=None,
        )


_KNOWN_PLANNER_ACTIONS = {
    "buy_pack",
    "buy_shop",
    "buy_voucher",
    "cash_out",
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

_PLANNER_ACTION_ALIASES = {
    "buy": "buy_shop",
    "buy shop": "buy_shop",
    "buyshop": "buy_shop",
    "buy pack": "buy_pack",
    "buypack": "buy_pack",
    "buy voucher": "buy_voucher",
    "buyvoucher": "buy_voucher",
    "choose pack": "choose_pack",
    "choosepack": "choose_pack",
    "next_round": "continue",
    "next round": "continue",
    "pass": "continue",
    "wait": "continue",
    "noop": "continue",
    "no_op": "continue",
    "hold": "continue",
    "play": "play_hand",
    "play hand": "play_hand",
    "playhand": "play_hand",
    "rearrange joker": "rearrange_joker",
    "rearrangejoker": "rearrange_joker",
    "rearrange consumable": "rearrange_consumable",
    "rearrangeconsumable": "rearrange_consumable",
    "reroll_shop": "reroll",
    "sell joker": "sell_joker",
    "selljoker": "sell_joker",
    "sell consumable": "sell_consumable",
    "sellconsumable": "sell_consumable",
    "skip blind": "skip_blind",
    "skipblind": "skip_blind",
    "skip pack": "skip_pack",
    "skippack": "skip_pack",
    "start": "start_run",
    "startrun": "start_run",
    "use": "use_consumable",
    "use consumable": "use_consumable",
    "useconsumable": "use_consumable",
    "cash": "cash_out",
    "cash out": "cash_out",
    "cashout": "cash_out",
}

_PLANNER_FIELD_NAME_TOKENS = {
    "action",
    "index",
    "indices",
    "reasoning",
    "synergy_evaluation",
}

# Local model reference — mirrors iris_agent.py LLM_MODEL
_LLM_MODEL = "qwen2.5:latest"

# ---------------------------------------------------------------------------
# Planner — Silent JSON Action Calculator
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are a silent Balatro game calculator. Your only job is to output a single JSON object.

OUTPUT FORMAT (strict — no other text, no markdown):
{
    "synergy_evaluation": "<1-2 sentence fit analysis using card value.effect text and current build effects before deciding>",
  "action": "<exact_action_name>",
  "indices": [<1-based integers>],
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
- "synergy_evaluation" must be written first and must explicitly evaluate how the candidate shop card's value.effect compounds with current owned Joker effects and current hand/build direction.
- "action" must be one action verb from the allowed-action list in context (examples: buy_shop, play_hand, continue).
- JSON key names are not actions. Never set action to "indices", "reasoning", or "synergy_evaluation".
- "indices" contains 1-based card or item positions. Use empty array [] if not needed.
- "reasoning" is brief internal logic. Keep it short and factual.
- Output ONLY the raw JSON object. Nothing else."""

# Run profile: populated when a new run starts, cleared on game over.
# Holds { "deck": str, "directive": str } for the duration of the run.
_run_profile: dict = {}


class PlannerOutput(BaseModel):
    """Strict planner response contract."""

    model_config = ConfigDict(extra="ignore")

    synergy_evaluation: str = ""
    action: str

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action_aliases(cls, value: Any) -> str:
        """Canonicalize planner action aliases at parse boundary."""
        text = str(value or "").strip().lower()
        if not text:
            return ""
        alias = _PLANNER_ACTION_ALIASES.get(text)
        if alias:
            return alias
        normalized = text.replace("-", "_").replace(" ", "_")
        return _PLANNER_ACTION_ALIASES.get(normalized, normalized)

    @field_validator("synergy_evaluation", mode="before")
    @classmethod
    def _coerce_synergy_to_str(cls, v: Any) -> str:
        """Coerce non-string synergy_evaluation to a plain string.

        The LLM sometimes returns a list or dict when evaluating multiple
        shop items simultaneously. Without coercion, Pydantic rejects the
        entire output and falls back to action=continue, silently skipping
        the shop. We stringify the value so the rest of the output is used.
        """
        if isinstance(v, str):
            return v
        if isinstance(v, (list, dict)):
            import json as _json

            try:
                return _json.dumps(v, ensure_ascii=False)
            except Exception:
                return str(v)
        return str(v) if v is not None else ""

    indices: list[int] = Field(default_factory=list)
    reasoning: str = ""

    INDEX_ALIASES: ClassVar[tuple[str, ...]] = (
        "index",
        "shop_index",
        "pack_index",
        "voucher_index",
        "consumable_index",
        "joker_index",
        "card_index",
        "target_index",
        "shop_indices",
        "pack_indices",
        "voucher_indices",
        "consumable_indices",
        "joker_indices",
        "card_indices",
        "target_indices",
    )

    @model_validator(mode="before")
    @classmethod
    def _handle_singular_index(cls, data: Any) -> Any:
        """Map planner index aliases to canonical `indices` before validation."""
        if isinstance(data, dict) and not data.get("indices"):
            for key in cls.INDEX_ALIASES:
                if key not in data:
                    continue
                value = data.pop(key)
                if value is None:
                    data["indices"] = []
                elif isinstance(value, list):
                    data["indices"] = value
                elif isinstance(value, tuple):
                    data["indices"] = list(value)
                else:
                    data["indices"] = [value]
                break
        return data

    @field_validator("indices", mode="before")
    @classmethod
    def _coerce_indices(cls, value: Any) -> list[int]:
        if value is None:
            return []
        if isinstance(value, tuple):
            value = list(value)
        elif isinstance(value, (int, str)):
            value = [value]

        if not isinstance(value, list):
            raise ValueError("indices must be a list of integers")

        coerced: list[int] = []
        for item in value:
            if isinstance(item, int):
                coerced.append(item)
                continue
            if isinstance(item, str):
                token = item.strip()
                if token.isdigit():
                    coerced.append(int(token))
                    continue
            raise ValueError("indices must contain only integers")

        return coerced

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
            invalid = [
                idx for idx in self.indices if not isinstance(idx, int) or idx < 1
            ]
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
    "ABANDONED": "No face cards (J, Q, K) in the deck. Straights and low-rank synergies easier. Prefer reliable scoring over delayed scaler jokers.",
    "CHECKERED": "Deck is 26 Spades + 26 Hearts only. Focus on FLUSH hands. PRIORITY: Buy Jupiter Planet (scales Flush), suit-specific Jokers (Heart/Spade effects like Bloodstone, Castle, Arrowhead).",
    "ZODIAC": "Start with Tarot Merchant, Planet Merchant, and Overstock. The shop is your best friend; keep money ready for frequent Tarot and Planet appearances.",
    "PAINTED": "+2 hand size but -1 Joker slot. You have 4 slots total. Use the large hand size to build difficult hands like Straights or Five-of-a-Kind.",
    "ANAGLYPH": "Boss Blinds grant Double Tags. Skip intelligently later in the run to stack multiple high-value rewards like Mega-Packs or Negative Jokers.",
    "PLASMA": "Balance Chips and Mult when scoring (e.g., 50 Chips and 50 Mult becomes 2500). Focus on stacking whichever stat is currently lower to maximize the product.",
    "ERRATIC": "Randomized ranks and suits. Check your deck composition immediately in Ante 1 to see which ranks or suits are naturally dominant.",
}

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


def _log_decision_safe(
    action: str,
    api_result: bool,
    error: str | None,
    raw_state: dict,
    planner_meta: dict[str, Any] | None = None,
) -> None:
    """Best-effort telemetry hook for planner decisions."""
    try:
        from skills.balatro_bot.modules.balatro_telemetry import log_decision

        log_decision(action, api_result, error, raw_state, planner_meta=planner_meta)
    except Exception as exc:
        log_system(f"[Balatro] Telemetry log_decision failed: {exc}")


def _normalize_action_token(raw_action: Any) -> str:
    """Best-effort canonicalization for planner action tokens."""
    text = str(raw_action or "").strip().lower()
    if not text:
        return ""
    alias = _PLANNER_ACTION_ALIASES.get(text)
    if alias:
        return alias
    normalized = text.replace("-", "_").replace(" ", "_")
    return _PLANNER_ACTION_ALIASES.get(normalized, normalized)


def _sanitize_planner_action(
    parsed: dict,
    allowed_actions: set[str] | None = None,
    phase_id: str = "",
    raw_action: str = "",
) -> tuple[dict, str | None, dict[str, Any]]:
    """Enforce action policy and deterministically fallback when invalid."""
    sanitized = dict(parsed)
    normalized_action = str(sanitized.get("action") or "").strip().lower()
    planner_meta: dict[str, Any] = {
        "phase_id": str(phase_id or ""),
        "raw_action": str(raw_action or normalized_action),
        "normalized_action": str(normalized_action or ""),
        "allowed_actions": sorted(allowed_actions) if allowed_actions else [],
        "fallback_applied": False,
    }

    if normalized_action in _PLANNER_FIELD_NAME_TOKENS:
        reason = (
            f"Planner action '{planner_meta['raw_action']}' matched a JSON field name. "
            "Falling back to continue."
        )
        sanitized["action"] = "continue"
        sanitized["indices"] = []
        planner_meta["fallback_applied"] = True
        planner_meta["fallback_reason"] = reason
        return sanitized, reason, planner_meta

    if normalized_action not in _KNOWN_PLANNER_ACTIONS:
        reason = (
            f"Planner action '{planner_meta['raw_action']}' normalized to '{normalized_action}', "
            "which is unknown. Falling back to continue."
        )
        sanitized["action"] = "continue"
        sanitized["indices"] = []
        planner_meta["fallback_applied"] = True
        planner_meta["fallback_reason"] = reason
        return sanitized, reason, planner_meta

    if allowed_actions is not None and normalized_action not in allowed_actions:
        reason = (
            f"Planner action '{normalized_action}' is not allowed in this phase. "
            "Falling back to continue."
        )
        sanitized["action"] = "continue"
        sanitized["indices"] = []
        planner_meta["fallback_applied"] = True
        planner_meta["fallback_reason"] = reason
        return sanitized, reason, planner_meta

    sanitized["action"] = normalized_action
    return sanitized, None, planner_meta


def _call_planner(
    planner_context: str,
    allowed_actions: set[str] | None = None,
    phase_id: str = "",
) -> dict:
    """Call Qwen as a silent JSON planner. Always returns a valid action dict.

    Uses temperature=0.0 and format='json' for deterministic structured output.
    Strips markdown fences if the model wraps output in them.
    Falls back to {"action": "continue", ...} on any parse failure.
    """
    default_planner_meta = {
        "phase_id": str(phase_id or ""),
        "raw_action": "",
        "normalized_action": "",
        "allowed_actions": sorted(allowed_actions) if allowed_actions else [],
        "fallback_applied": True,
        "fallback_reason": "planner parse/validation failure",
    }
    _DEFAULT = PlannerOutput(
        synergy_evaluation="",
        action="continue",
        indices=[],
        reasoning="Fallback: planner output could not be parsed.",
    ).model_dump()
    _DEFAULT["_planner_telemetry"] = dict(default_planner_meta)
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
        planner_raw_action = ""
        if isinstance(parsed_json, dict):
            planner_raw_action = str(parsed_json.get("action") or "")

        try:
            parsed = PlannerOutput.model_validate(parsed_json).model_dump()
        except ValidationError as ve:
            log_system(f"[Planner] Validation failed: {ve}. Raw: {raw[:120]}")
            if isinstance(parsed_json, dict):
                _DEFAULT["_planner_telemetry"]["raw_action"] = str(
                    parsed_json.get("action") or ""
                )
                _DEFAULT["_planner_telemetry"]["normalized_action"] = (
                    _normalize_action_token(parsed_json.get("action"))
                )
                _DEFAULT["_planner_telemetry"]["fallback_reason"] = (
                    "planner schema validation failure"
                )
            return _DEFAULT

        parsed, fallback_reason, planner_meta = _sanitize_planner_action(
            parsed,
            allowed_actions,
            phase_id,
            raw_action=planner_raw_action,
        )
        parsed["_planner_telemetry"] = planner_meta
        if fallback_reason:
            log_system(f"[Planner] Post-parse fallback: {fallback_reason}")

        log_system(
            f"[Planner] action={parsed['action']!r} "
            f"indices={parsed['indices']} "
            f"synergy={parsed['synergy_evaluation'][:80]} "
            f"reasoning={parsed['reasoning'][:80]}"
        )
        return parsed
    except Exception as exc:
        if "raw" in locals() and isinstance(raw, str):
            _DEFAULT["_planner_telemetry"]["fallback_reason"] = (
                f"planner parse exception: {exc}"
            )
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
        self._last_logged_hand_str: str = ""
        self._last_ante_num: int = 0
        self._blocked_actions_for_state: dict[str, str] = {}
        self._blocked_actions_phase: str | None = None
        self._action_retry_counts_for_phase: dict[str, int] = {}

        # New modular components (ActionRegistry imported lazily in run_loop to avoid circular import)
        self.phase_router = PhaseRouter()
        self.prompt_builder = PlannerContextBuilder(self.action_memory)

    def _reset_state_action_blocks(self, current_phase: str) -> None:
        """Clear blocked-action memory only when the game phase (state name) changes.

        Uses the raw state name string rather than the full state hash so that minor
        cosmetic state updates (animation counters, timestamps) do not accidentally
        wipe the block list while the bot is still in the same logical game phase.
        """
        if current_phase != self._blocked_actions_phase:
            self._blocked_actions_phase = current_phase
            self._blocked_actions_for_state.clear()
            self._action_retry_counts_for_phase.clear()

    def _attempt_state_aware_recovery(
        self, controller: GameController, raw_state: dict
    ) -> tuple[bool, str]:
        """Attempt a safe recovery based on the current state."""
        current_state = (raw_state.get("state", "") or "").lower()

        if "round_eval" in current_state:
            for _ in range(2):
                if controller.client._call("cash_out") is not None:
                    return True, "Recovered by cash_out in round_eval."
                time.sleep(1.0)
            api_error = (
                getattr(controller.client, "last_error", "") or "Unknown API error"
            )
            return False, f"round_eval recovery failed: {api_error}"

        if "shop" in current_state:
            # Economy-preserving hard exit: never auto-reroll on repeated shop errors.
            if controller.client.proceed_next():
                return (
                    True,
                    "Recovered by next_round in shop (circuit breaker hard-exit).",
                )
            api_error = (
                getattr(controller.client, "last_error", "") or "Unknown API error"
            )
            return False, f"shop recovery failed: {api_error}"

        if "blind" in current_state:
            if controller.client.select() or controller.client.skip():
                return True, "Recovered by blind select/skip."
            api_error = (
                getattr(controller.client, "last_error", "") or "Unknown API error"
            )
            return False, f"blind recovery failed: {api_error}"

        if (
            "play" in current_state
            or "hand" in current_state
            or "selecting_hand" in current_state
        ):
            # Attempt to force-play the algorithm's best hand to physically advance
            # the game state. A simple state refresh is not enough — the planner would
            # re-run and attempt the same blocked action again on the very next tick.
            hand_cards: list[dict] = []
            hand_data = raw_state.get("hand", {})
            if isinstance(hand_data, dict):
                hand_cards = hand_data.get("cards", [])

            if hand_cards:
                best_play_data = BalatroAlgorithm.find_best_hand(hand_cards)
                options = best_play_data.get("options", [])
                if options:
                    best_indices_1based = options[0].get("indices", [])
                    best_hand_name = options[0].get("hand_name", "hand")
                    zero_based = [i - 1 for i in best_indices_1based if i >= 1]
                    if zero_based:
                        success, _err = controller.client.play_hand(zero_based)
                        if success:
                            return (
                                True,
                                f"Softlock: force-played {best_hand_name} "
                                f"(indices {best_indices_1based}) to break loop.",
                            )

                # Fallback: force-discard the first card to unstick the game
                success, _err = controller.client.discard([0])
                if success:
                    return (
                        True,
                        "Softlock: force-discarded index 0 as fallback to break loop.",
                    )

            controller.refresh_state()
            return True, "Refreshed state in play/hand; forcing planner re-evaluation."

        return False, f"No recovery action available for state '{current_state}'."

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
                            f"[Balatro] SOFTLOCK DETECTED ({self._stall_ticks} stall ticks) — attempting state-aware recovery."
                        )
                        recovered, recovery_msg = self._attempt_state_aware_recovery(
                            controller, controller.raw_state
                        )
                        log_system(
                            f"[Balatro] Softlock recovery {'succeeded' if recovered else 'failed'}: {recovery_msg}"
                        )
                        self._stall_ticks = 0
                        # Force a planner re-evaluation after softlock handling, even if
                        # recovery did not transition state, to avoid dead waiting loops.
                        self.last_state_hash = None
                    else:
                        time.sleep(5)
                    continue
                self._stall_ticks = 0
                self.last_state_hash = current_hash
                current_state_lower_for_phase = (
                    controller.raw_state.get("state", "") or ""
                ).lower()
                self._reset_state_action_blocks(current_state_lower_for_phase)

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

                # --- Detect newly drawn cards only in interactive hand phases ---
                new_ante = raw_state.get("ante_num", 1)
                if new_ante != self._last_ante_num:
                    self._last_hand_cards = []
                    self._last_logged_hand_str = ""
                    self._last_ante_num = new_ante
                    log_system(f"[Balatro] --- Ante {new_ante} started ---")

                interactive_hand_phase = (
                    "play" in current_state_lower
                    or "hand" in current_state_lower
                    or "selecting_hand" in current_state_lower
                )
                if not interactive_hand_phase:
                    self._last_logged_hand_str = ""
                    self._last_hand_cards = []
                else:
                    current_hand_str = _format_hand(hand_cards)
                    if current_hand_str != self._last_logged_hand_str:
                        if self._last_hand_cards:
                            old_counts = collections.Counter(
                                _format_card(c) for c in self._last_hand_cards
                            )
                            new_counts = collections.Counter(
                                _format_card(c) for c in hand_cards
                            )
                            drawn_counts = new_counts - old_counts
                            drawn_cards = [
                                card
                                for card, count in drawn_counts.items()
                                for _ in range(count)
                            ]
                            if drawn_cards:
                                log_system(f"[Balatro] Drawn: {', '.join(drawn_cards)}")

                        log_system(f"[Balatro] Hand: {current_hand_str}")
                        self._last_logged_hand_str = current_hand_str
                        self._last_hand_cards = list(hand_cards)

                # --- Resolve active boss debuff filters (used by algo AND LLM context) ---
                active_boss_constraint = ""
                active_debuffed_ranks: list[str] = []
                active_debuffed_suits: list[str] = []
                # Debuff lists are intentionally empty outside play/hand states.
                # Shop/menu/blind logic should not inherit stale boss debuff filters.
                if "play" in current_state_lower or "hand" in current_state_lower:
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
                    deck_name = (
                        self.run_profile.get("deck", "") if self.run_profile else ""
                    )
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
                consumables = _get_state_cards(raw_state, "consumeables")
                if consumables:
                    deck_name = (
                        self.run_profile.get("deck", "") if self.run_profile else ""
                    )
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
                    deck_name = (
                        self.run_profile.get("deck", "") if self.run_profile else ""
                    )
                    shop_cache = tick_context.ensure_shop_cache(deck_name)
                    strategy_block = _build_shop_strategy_block(
                        raw_state,
                        hand_cards,
                        deck_name=deck_name,
                        debuffed_ranks=active_debuffed_ranks,
                        debuffed_suits=active_debuffed_suits,
                        allows_four_card_hands=allows_four_card_hands,
                        allows_gaps=allows_gaps,
                        shop_cache=shop_cache,
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
                    # Derive flush commitment from the top hand option already evaluated above.
                    # "Flush" in best_hand_name catches Flush, Straight Flush, Flush Five, etc.
                    _discard_target_type = (
                        best_hand_name if "Flush" in best_hand_name else None
                    )
                    discard_suggestion = BalatroAlgorithm.find_best_discard(
                        hand_cards,
                        card_indices_to_play,
                        target_hand_type=_discard_target_type,
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
                    blocked_actions=dict(self._blocked_actions_for_state)
                    if self._blocked_actions_for_state
                    else None,
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
                    self._last_filler_time = _now

                # --- Step 3: Call silent Planner ---
                planner_allowed_actions = (
                    set(phase_directive.full_allowed_actions)
                    if phase_directive is not None
                    else None
                )
                planner_phase_id = (
                    str(phase_directive.phase_id) if phase_directive is not None else ""
                )
                planner = _call_planner(
                    planner_context,
                    allowed_actions=planner_allowed_actions,
                    phase_id=planner_phase_id,
                )
                planner_meta = planner.get("_planner_telemetry")
                action_tag = planner["action"].lower().strip().replace(" ", "_")
                args = planner["indices"]  # 1-based int list from Planner JSON
                action_scope = (
                    ",".join(str(int(x)) for x in args)
                    if isinstance(args, list) and args
                    else ""
                )
                action_attempt_key = (
                    f"{action_tag}[{action_scope}]" if action_scope else action_tag
                )

                # Override card_indices_to_play with planner choice for play_hand
                # (algo result stays as fallback if planner returns empty indices)
                if action_tag == "play_hand" and args:
                    card_indices_to_play = args

                log_system(f"[Balatro] Planner: action={action_tag!r} indices={args}")

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
                    f"[Balatro] Executing action: {action_tag!r} with args: {args!r}"
                )

                current_state = (controller.raw_state.get("state", "") or "").lower()

                if phase_directive is None and "game_over" not in current_state:
                    log_system(
                        f"[ROUTER] No phase directive for state '{current_state}'. "
                        "Forcing continue fallback for safety."
                    )
                    action_tag = "continue"
                    args = []
                    action_attempt_key = "continue"

                # Suppress immediate retries of actions that already failed in this exact state hash.
                blocked_reason = self._blocked_actions_for_state.get(action_attempt_key)
                if blocked_reason:
                    log_system(
                        f"[Balatro] Suppressing repeated action '{action_attempt_key}' in unchanged state. "
                        f"Prior failure: {blocked_reason}"
                    )
                    recovered, recovery_msg = self._attempt_state_aware_recovery(
                        controller, controller.raw_state
                    )
                    memory_msg = (
                        f"Action: [{action_tag}] -> Suppressed repeat in unchanged state. "
                        f"Recovery {'ok' if recovered else 'failed'}: {recovery_msg}"
                    )
                    self.action_memory.append(memory_msg)
                    _log_decision_safe(
                        action_tag,
                        False,
                        memory_msg,
                        raw_state,
                        planner_meta=planner_meta,
                    )
                    # Always force a re-plan after suppressing a blocked action.
                    self.last_state_hash = None
                    time.sleep(1)
                    continue

                retry_count = (
                    self._action_retry_counts_for_phase.get(action_attempt_key, 0) + 1
                )
                self._action_retry_counts_for_phase[action_attempt_key] = retry_count
                if retry_count > 3:
                    decision_error = (
                        f"Action Denied: '{action_attempt_key}' exceeded retry limit (3) in this phase. "
                        "Choose a different action."
                    )
                    self._blocked_actions_for_state[action_attempt_key] = decision_error
                    self.action_memory.append(
                        f"Action: [{action_tag}] -> Error: {decision_error}"
                    )
                    log_system(f"[Balatro] {decision_error}")
                    api_succeeded = False
                    _log_decision_safe(
                        action_tag,
                        False,
                        decision_error,
                        raw_state,
                        planner_meta=planner_meta,
                    )
                    self.last_state_hash = None
                    time.sleep(1)
                    continue

                # --- Special case: GAME OVER ---
                if "game_over" in current_state:
                    log_system(
                        "[Balatro] Game Over detected. Logging death and triggering menu."
                    )
                    self._last_logged_hand_str = ""
                    self._last_hand_cards = []
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
                    self._blocked_actions_for_state[action_attempt_key] = decision_error
                    self.action_memory.append(
                        f"Action: [{action_tag}] -> Error: {decision_error}"
                    )
                    api_succeeded = False
                    _log_decision_safe(
                        action_tag,
                        False,
                        decision_error,
                        raw_state,
                        planner_meta=planner_meta,
                    )
                    # Force immediate planner re-evaluation instead of entering a stall window.
                    self.last_state_hash = None
                    time.sleep(1)
                    continue

                if action_tag not in _KNOWN_PLANNER_ACTIONS:
                    decision_error = f"Unsupported planner action '{action_tag}' in state '{current_state}'."
                    log_system(f"[Balatro] {decision_error}")
                    self.action_memory.append(
                        f"Action: [{action_tag}] -> Error: {decision_error}"
                    )
                    api_succeeded = False
                    _log_decision_safe(
                        action_tag,
                        False,
                        decision_error,
                        raw_state,
                        planner_meta=planner_meta,
                    )
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
                        self._action_retry_counts_for_phase.pop(
                            action_attempt_key, None
                        )
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

                        if decision_error:
                            lowered_error = decision_error.lower()
                            if (
                                "invalid_state" in lowered_error
                                or "requires one of these states" in lowered_error
                                or "blocked in state" in lowered_error
                                or "not allowed in phase" in lowered_error
                                or "insufficient funds" in lowered_error
                                or "cannot afford" in lowered_error
                                or "action denied" in lowered_error
                                or "safety override" in lowered_error
                                or "bad synergy" in lowered_error
                                or "retry limit" in lowered_error
                            ):
                                self._blocked_actions_for_state[action_attempt_key] = (
                                    decision_error
                                )

                        if error_feedback:
                            self.action_memory.append(error_feedback)

                        # Force immediate re-plan after any failed action so we
                        # do not idle on unchanged state until softlock recovery.
                        self.last_state_hash = None

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
                    _log_decision_safe(
                        action_tag,
                        False,
                        decision_error,
                        raw_state,
                        planner_meta=planner_meta,
                    )
                    time.sleep(1)
                    continue

                _log_decision_safe(
                    action_tag,
                    api_succeeded,
                    decision_error,
                    raw_state,
                    planner_meta=planner_meta,
                )

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
