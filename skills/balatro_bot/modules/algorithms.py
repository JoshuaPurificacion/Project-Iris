import itertools
import re
from collections import Counter
from typing import List, Dict, Any, Optional

# Map Balatro's string ranks to sortable integer values
RANK_VALUES = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "T": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
    "A": 14,
}

RANK_WORD_ALIASES = {
    "ace": "A",
    "king": "K",
    "queen": "Q",
    "jack": "J",
    "ten": "T",
    "nine": "9",
    "eight": "8",
    "seven": "7",
    "six": "6",
    "five": "5",
    "four": "4",
    "three": "3",
    "two": "2",
}

SUIT_TOKENS = {
    "spade": "spades",
    "spades": "spades",
    "heart": "hearts",
    "hearts": "hearts",
    "club": "clubs",
    "clubs": "clubs",
    "diamond": "diamonds",
    "diamonds": "diamonds",
}

XMULT_REGEX_PATTERNS = (
    r"(?<![\w+])(?:x|X|×)\s*(\d+(?:\.\d+)?)\s*mult\b",
    r"×\s*(\d+(?:\.\d+)?)\b",
)

UNRELIABLE_JOKER_PATTERNS = (
    r"\b1\s*in\s*\d+\b",
    r"\bchance\b",
    r"\brandom\b",
    r"\bdestroy(?:ed)?\s+at\s+end\s+of\s+round\b",
    r"\bdestroy\s+random\s+joker\b",
)

# Hard safety blacklist for joker keys the planner should never buy.
FORCED_BAD_SYNERGY_JOKER_KEYS = {
    "j_ride_the_bus",
    "j_blackboard",
    "j_runner",
    "j_green_joker",
    "j_red_card",
    "j_square",
    "j_fortune_teller",
}


# Base Chips and Mult for each poker hand type
BASE_VALUES = {
    "Royal Flush": (100, 8),
    "Straight Flush": (100, 8),
    "Flush Five": (160, 16),
    "Five of a Kind": (120, 12),
    "Four of a Kind": (60, 7),
    "Full House": (40, 4),
    "Flush": (35, 4),
    "Straight": (30, 4),
    "Three of a Kind": (30, 3),
    "Two Pair": (20, 2),
    "Pair": (10, 2),
    "High Card": (5, 1),
}


class BalatroAlgorithm:
    @staticmethod
    def _extract_explicit_level_maps(
        deck_state: Optional[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Read explicit level metadata from deck state if available."""
        if not isinstance(deck_state, dict):
            return None, None

        hand_levels = deck_state.get("hand_levels")
        planet_levels = deck_state.get("planet_levels")
        if not isinstance(hand_levels, dict):
            hand_levels = None
        if not isinstance(planet_levels, dict):
            planet_levels = None
        return hand_levels, planet_levels

    @staticmethod
    def _extract_level_value(levels: Optional[Dict[str, Any]], hand_name: str) -> int:
        """Resolve explicit hand/planet level from API metadata if present."""
        if not isinstance(levels, dict):
            return 1

        candidates = [hand_name, hand_name.lower(), hand_name.upper()]
        for key in candidates:
            if key in levels:
                raw = levels.get(key)
                if isinstance(raw, dict):
                    raw = raw.get("level", raw.get("value", 1))
                try:
                    lvl = int(raw)
                    return max(1, lvl)
                except (TypeError, ValueError):
                    continue
        return 1

    @staticmethod
    def _apply_hand_level_scaling(
        hand_name: str,
        base_chips: float,
        base_mult: float,
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> tuple[float, float]:
        """Apply explicit level-driven scaling to base hand values.

        Uses only explicit API metadata. If no metadata is present, values are unchanged.
        """
        explicit_level = max(
            BalatroAlgorithm._extract_level_value(hand_levels, hand_name),
            BalatroAlgorithm._extract_level_value(planet_levels, hand_name),
        )
        if explicit_level <= 1:
            return base_chips, base_mult

        level_steps = explicit_level - 1
        scaled_chips = base_chips + (10.0 * level_steps)
        scaled_mult = base_mult + (1.0 * level_steps)
        return scaled_chips, scaled_mult

    @staticmethod
    def _state_cards(
        raw_state: Optional[Dict[str, Any]], container_name: str
    ) -> List[Dict[str, Any]]:
        """Safely read a card list from a Balatro state container."""
        if not isinstance(raw_state, dict):
            return []

        candidate_names = [container_name]
        if container_name == "consumeables":
            candidate_names.append("consumables")
        elif container_name == "consumables":
            candidate_names.append("consumeables")

        for name in candidate_names:
            container = raw_state.get(name) or {}
            if not isinstance(container, dict):
                continue

            cards = container.get("cards", [])
            if not isinstance(cards, list):
                continue

            return [card for card in cards if isinstance(card, dict)]

        return []

    @staticmethod
    def _card_enhancement(card: Dict[str, Any]) -> str:
        """Best-effort enhancement reader from card modifier payloads."""
        modifier = card.get("modifier")
        if isinstance(modifier, dict):
            return str(modifier.get("enhancement") or "").upper()

        if isinstance(modifier, list):
            for item in modifier:
                if isinstance(item, dict) and item.get("enhancement"):
                    return str(item.get("enhancement") or "").upper()

        return ""

    @staticmethod
    def _collect_scaling_state(
        deck_state: Optional[Dict[str, Any]],
        hand_cards: Optional[List[Dict[str, Any]]],
        current_jokers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Collect lightweight state needed for scaling-joker heuristics."""
        visible_cards: List[Dict[str, Any]] = []
        for container in ("deck", "hand", "discard"):
            visible_cards.extend(BalatroAlgorithm._state_cards(deck_state, container))
        if not visible_cards:
            visible_cards = [
                card for card in (hand_cards or []) if isinstance(card, dict)
            ]

        enhancements = [
            BalatroAlgorithm._card_enhancement(card) for card in visible_cards
        ]
        steel_cards = sum(1 for enh in enhancements if enh == "STEEL")
        enhanced_cards = sum(1 for enh in enhancements if bool(enh))

        money = 0
        if isinstance(deck_state, dict):
            try:
                money = int(deck_state.get("money") or 0)
            except (TypeError, ValueError):
                money = 0

        vouchers = BalatroAlgorithm._state_cards(deck_state, "vouchers")
        packs = BalatroAlgorithm._state_cards(deck_state, "packs")
        consumables = BalatroAlgorithm._state_cards(deck_state, "consumables")
        voucher_text = " ".join(BalatroAlgorithm._card_text(v) for v in vouchers)
        consumable_text = " ".join(BalatroAlgorithm._card_text(c) for c in consumables)

        joker_text = " ".join(BalatroAlgorithm._card_text(j) for j in current_jokers)
        has_midas_support = "midas" in joker_text

        return {
            "steel_cards": steel_cards,
            "enhanced_cards": enhanced_cards,
            "money": money,
            "pack_count": len(packs),
            "has_magic_trick": ("magic trick" in voucher_text)
            or ("magic_trick" in voucher_text),
            "has_illusion": "illusion" in voucher_text,
            "has_chariot": "chariot" in consumable_text,
            "has_midas_support": has_midas_support,
        }

    @staticmethod
    def _estimate_scaling_joker_bonus(
        joker: Dict[str, Any],
        current_jokers: List[Dict[str, Any]],
        deck_state: Optional[Dict[str, Any]] = None,
        deck_name: str = "",
        hand_cards: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        """Estimate long-horizon value for conditional scaling jokers."""
        text = BalatroAlgorithm._card_text(joker)
        label = str(joker.get("label") or "").lower()
        key = str(joker.get("key") or "").lower()
        state = BalatroAlgorithm._collect_scaling_state(
            deck_state, hand_cards, current_jokers
        )

        bonus = 0.0

        # Steel Joker-style growth from Steel cards in deck.
        if "steel" in text and ("joker" in text or key.startswith("j_")):
            steel_cards = int(state.get("steel_cards", 0))
            bonus += 8.0 + (steel_cards * 8.0)
            if state.get("has_chariot"):
                bonus += 8.0
            if state.get("has_midas_support"):
                bonus += 6.0

        # Hologram-style growth from adding new cards to deck.
        if "hologram" in label or "hologram" in key or "hologram" in text:
            pack_count = int(state.get("pack_count", 0))
            money = int(state.get("money", 0))
            bonus += 10.0 + (min(4, pack_count) * 6.0)
            if state.get("has_magic_trick"):
                bonus += 10.0
            if state.get("has_illusion"):
                bonus += 6.0
            if money >= 25:
                bonus += 6.0

        # Vampire-style growth from enhanced cards in deck.
        if "vampire" in label or "vampire" in key or "vampire" in text:
            enhanced_cards = int(state.get("enhanced_cards", 0))
            bonus += enhanced_cards * 5.0

        # Ride the Bus gets extra value in Abandoned strategy.
        if "ride" in label and "bus" in label:
            bonus += 10.0

        deck_upper = deck_name.upper() if deck_name else ""
        if deck_upper == "CHECKERED":
            if "flush" in text:
                bonus += 10.0
            if "heart" in text or "spade" in text:
                bonus += 8.0

        return bonus

    @staticmethod
    def _is_tarot_card(card: Dict[str, Any]) -> bool:
        """Best-effort Tarot detector for shop/pack/consumable payloads."""
        key = str(card.get("key") or "").lower()
        label = str(card.get("label") or "").lower()
        return key.startswith("c_tarot") or "tarot" in key or "tarot" in label

    @staticmethod
    def _count_visible_tarot_cards(deck_state: Optional[Dict[str, Any]]) -> int:
        """Count visible Tarot cards across shop-like containers."""
        count = 0
        for container_name in ("shop", "packs", "consumables", "consumeables"):
            for card in BalatroAlgorithm._state_cards(deck_state, container_name):
                if isinstance(card, dict) and BalatroAlgorithm._is_tarot_card(card):
                    count += 1
        return count

    @staticmethod
    def _lineup_strategic_bonus(
        jokers: List[Dict[str, Any]],
        deck_state: Optional[Dict[str, Any]] = None,
        deck_name: str = "",
        hand_cards: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Aggregate strategic scaling value for a full joker lineup."""
        total = 0.0
        for joker in jokers:
            if not isinstance(joker, dict):
                continue
            total += BalatroAlgorithm._estimate_scaling_joker_bonus(
                joker,
                jokers,
                deck_state=deck_state,
                deck_name=deck_name,
                hand_cards=hand_cards,
            )
        return int(round(total))

    @staticmethod
    def _normalize_card_suit(card: Dict[str, Any]) -> str:
        """Normalize suit token to lowercase plural form."""
        suit = str((card.get("value") or {}).get("suit") or "").strip().lower()
        return SUIT_TOKENS.get(suit, suit)

    @staticmethod
    def _normalize_card_rank(card: Dict[str, Any]) -> str:
        """Normalize rank token to standard short form (A,K,Q,J,T,2-9)."""
        rank = str((card.get("value") or {}).get("rank") or "").strip().upper()
        if rank in RANK_VALUES:
            return rank
        lowered = rank.lower()
        return RANK_WORD_ALIASES.get(lowered, rank)

    @staticmethod
    def _estimate_draw_probability(
        deck_state: Optional[Dict[str, Any]],
        hand_cards: Optional[List[Dict[str, Any]]],
        suit: Optional[str] = None,
        rank: Optional[str] = None,
        draws: int = 5,
    ) -> float:
        """Estimate expected trigger count from suit/rank draws."""
        pool_cards = BalatroAlgorithm._state_cards(deck_state, "deck")
        if not pool_cards:
            pool_cards = [card for card in (hand_cards or []) if isinstance(card, dict)]
        if not pool_cards:
            return 0.0

        normalized_suit = SUIT_TOKENS.get(
            str(suit or "").lower(), str(suit or "").lower()
        )
        normalized_rank = str(rank or "").upper()
        if normalized_rank:
            normalized_rank = RANK_WORD_ALIASES.get(
                normalized_rank.lower(), normalized_rank
            )

        total = 0
        matches = 0
        for card in pool_cards:
            if not isinstance(card, dict):
                continue
            total += 1
            card_suit = BalatroAlgorithm._normalize_card_suit(card)
            card_rank = BalatroAlgorithm._normalize_card_rank(card)
            suit_ok = normalized_suit == "" or card_suit == normalized_suit
            rank_ok = normalized_rank == "" or card_rank == normalized_rank
            if suit_ok and rank_ok:
                matches += 1

        if total <= 0:
            return 0.0

        ratio = max(0.0, min(1.0, matches / float(total)))
        expected_hits = ratio * float(max(1, draws))
        return max(0.0, min(float(max(1, draws)), expected_hits))

    @staticmethod
    def _estimate_joker_future_growth(
        joker: Dict[str, Any],
        deck_state: Optional[Dict[str, Any]],
        hand_cards: Optional[List[Dict[str, Any]]],
        rounds: int = 3,
    ) -> float:
        """Estimate expected additive score gain from scaling triggers across rounds."""
        effect = BalatroAlgorithm._card_text(joker)
        rounds = max(1, int(rounds))
        total_growth = 0.0

        per_hand_mult = BalatroAlgorithm._extract_first_number(
            effect,
            r"gains?\s*\+?(\d+(?:\.\d+)?)\s*mult\s*per\s*hand",
            0.0,
        )
        if per_hand_mult > 0:
            total_growth += per_hand_mult * rounds

        for suit_token, normalized_suit in SUIT_TOKENS.items():
            if f"per {suit_token}" in effect:
                gain = BalatroAlgorithm._extract_first_number(
                    effect,
                    r"gains?\s*\+?(\d+(?:\.\d+)?)\s*mult\s*per",
                    0.0,
                )
                if gain > 0:
                    prob = BalatroAlgorithm._estimate_draw_probability(
                        deck_state,
                        hand_cards,
                        suit=normalized_suit,
                        draws=5,
                    )
                    total_growth += gain * prob * rounds
                break

        rank_alias_items = list(RANK_WORD_ALIASES.items())
        rank_alias_items.extend((r.lower(), r) for r in RANK_VALUES.keys())
        for rank_token, normalized_rank in rank_alias_items:
            if f"per {rank_token}" in effect:
                gain = BalatroAlgorithm._extract_first_number(
                    effect,
                    r"gains?\s*\+?(\d+(?:\.\d+)?)\s*mult\s*per",
                    0.0,
                )
                if gain > 0:
                    prob = BalatroAlgorithm._estimate_draw_probability(
                        deck_state,
                        hand_cards,
                        rank=normalized_rank,
                        draws=5,
                    )
                    total_growth += gain * prob * rounds
                break

        return max(0.0, total_growth)

    @staticmethod
    def lineup_has_probabilistic_jokers(jokers: List[Dict[str, Any]]) -> bool:
        """Detect jokers/effects that can legitimately produce high score variance."""
        rng_tokens = (
            "chance",
            "1 in",
            "random",
            "probability",
            "glass",
            "bloodstone",
            "space joker",
        )
        for joker in jokers:
            if not isinstance(joker, dict):
                continue
            text = BalatroAlgorithm._card_text(joker)
            if any(token in text for token in rng_tokens):
                return True
        return False

    @staticmethod
    def _is_unreliable_joker_effect(effect_text: str) -> bool:
        """Detect unreliable RNG/self-destruct joker descriptions."""
        text = str(effect_text or "")
        return any(
            re.search(pattern, text, re.IGNORECASE) is not None
            for pattern in UNRELIABLE_JOKER_PATTERNS
        )

    @staticmethod
    def _estimate_joker_immediate_additive_value(
        joker: Dict[str, Any],
        baseline_score: float = 0.0,
    ) -> float:
        """Estimate immediate additive value from flat +Mult/+Chips effects.

        When *baseline_score* is provided (> 0) and the joker has xMult hits,
        the xMult contribution is computed as the actual score delta it produces
        against the current board baseline rather than the flat 60.0 proxy.
        The flat fallback is preserved for callers that don't know the baseline.
        """
        effect = BalatroAlgorithm._card_text(joker)
        chips_hits = BalatroAlgorithm._extract_all_numbers(
            effect, r"\+(\d+(?:\.\d+)?)\s*chips?"
        )
        mult_hits = BalatroAlgorithm._extract_all_numbers(
            effect, r"\+(\d+(?:\.\d+)?)\s*mult"
        )
        xmult_hits = BalatroAlgorithm._extract_xmult_values(effect)
        chips_score = sum(chips_hits)
        mult_score = sum(mult_hits) * 1.5
        if baseline_score > 0.0 and xmult_hits:
            xmult_score = sum((hit - 1.0) * baseline_score for hit in xmult_hits)
        else:
            xmult_score = sum(max(0.0, hit - 1.0) * 60.0 for hit in xmult_hits)
        return chips_score + mult_score + xmult_score

    @staticmethod
    def _estimate_joker_projected_additive_value(
        joker: Dict[str, Any],
        deck_state: Optional[Dict[str, Any]],
        hand_cards: Optional[List[Dict[str, Any]]],
        rounds: int = 3,
    ) -> float:
        """Estimate projected additive value after short-horizon growth."""
        immediate = BalatroAlgorithm._estimate_joker_immediate_additive_value(joker)
        future = BalatroAlgorithm._estimate_joker_future_growth(
            joker,
            deck_state=deck_state,
            hand_cards=hand_cards,
            rounds=rounds,
        )
        return immediate + future

    @staticmethod
    def estimate_lineup_expected_score(
        hand_cards: List[Dict[str, Any]],
        jokers: List[Dict[str, Any]],
        deck_name: str = "",
        deck_state: Optional[Dict[str, Any]] = None,
        rounds: int = 3,
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Estimate expected score after short-horizon scaling growth."""
        base_score = BalatroAlgorithm.estimate_best_score(
            hand_cards,
            current_jokers=jokers,
            deck_name=deck_name,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
            hand_levels=hand_levels,
            planet_levels=planet_levels,
        )
        strategic_bonus = BalatroAlgorithm._lineup_strategic_bonus(
            jokers,
            deck_state=deck_state,
            deck_name=deck_name,
            hand_cards=hand_cards,
        )
        future_growth = 0.0
        for joker in jokers:
            if not isinstance(joker, dict):
                continue
            future_growth += BalatroAlgorithm._estimate_joker_future_growth(
                joker,
                deck_state=deck_state,
                hand_cards=hand_cards,
                rounds=rounds,
            )

        return int(round(base_score + strategic_bonus + future_growth))

    @staticmethod
    def _extract_first_number(text: str, pattern: str, default: float = 0.0) -> float:
        """Extract first numeric capture group from a regex pattern."""
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return default
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _extract_all_numbers(text: str, pattern: str) -> List[float]:
        """Extract all numeric capture groups from a regex pattern."""
        values: List[float] = []
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                values.append(float(match.group(1)))
            except (ValueError, TypeError):
                continue
        return values

    @staticmethod
    def _extract_xmult_values(text: str) -> List[float]:
        """Extract strict xMult values and reject flat '+N Mult' lookalikes."""
        values: List[float] = []
        for pattern in XMULT_REGEX_PATTERNS:
            values.extend(BalatroAlgorithm._extract_all_numbers(text, pattern))
        deduped: List[float] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        return deduped

    @staticmethod
    def _card_text(card: Dict[str, Any]) -> str:
        """Flatten a card's identifying text for lightweight heuristics."""
        value = card.get("value") or {}
        parts = [
            str(card.get("key") or ""),
            str(card.get("label") or ""),
            str(value.get("effect") or ""),
        ]
        return " ".join(part for part in parts if part).lower()

    @staticmethod
    def is_probable_joker(card: Dict[str, Any]) -> bool:
        """Best-effort Joker detector for shop and inventory heuristics."""
        if not isinstance(card, dict):
            return False

        key = str(card.get("key") or "").lower()
        label = str(card.get("label") or "").lower()
        card_type = str(card.get("type") or "").lower()
        area = str(card.get("area") or "").lower()

        return (
            key.startswith("j_")
            or "joker" in label
            or card_type == "joker"
            or area == "jokers"
        )

    @staticmethod
    def _is_blueprint_joker(joker: Dict[str, Any]) -> bool:
        text = BalatroAlgorithm._card_text(joker)
        return "blueprint" in text

    @staticmethod
    def _is_brainstorm_joker(joker: Dict[str, Any]) -> bool:
        text = BalatroAlgorithm._card_text(joker)
        return "brainstorm" in text

    @staticmethod
    def _is_copycat_joker(joker: Dict[str, Any]) -> bool:
        return BalatroAlgorithm._is_blueprint_joker(
            joker
        ) or BalatroAlgorithm._is_brainstorm_joker(joker)

    @staticmethod
    def _joker_sort_bucket(joker: Dict[str, Any]) -> int:
        """Categorize jokers for deterministic baseline ordering.

        Order is Economy/Utility -> Chips -> +Mult -> xMult.
        Hybrid jokers with any xMult text are treated as xMult and move right.
        """
        text = BalatroAlgorithm._card_text(joker)
        has_xmult = bool(BalatroAlgorithm._extract_xmult_values(text)) or (
            "xmult" in text
        )
        has_mult = bool(re.search(r"\+\s*\d+(?:\.\d+)?\s*mult|\bmult\b", text))
        has_chips = bool(re.search(r"\+\s*\d+(?:\.\d+)?\s*chips?|\bchips?\b", text))
        has_econ = any(tok in text for tok in ("money", "interest", "cash", "$"))

        if has_xmult:
            return 3
        if has_mult:
            return 2
        if has_chips:
            return 1
        if has_econ:
            return 0
        return 0

    @staticmethod
    def _joker_sort_key(joker: Dict[str, Any]) -> tuple[int, float]:
        """Sort key for baseline non-copycat joker ordering."""
        bucket = BalatroAlgorithm._joker_sort_bucket(joker)
        # Keep stronger cards later within each bucket so upstream additive bonuses
        # tend to be multiplied by right-side multipliers.
        magnitude = BalatroAlgorithm._estimate_joker_immediate_additive_value(joker)
        return (bucket, magnitude)

    @staticmethod
    def _resolve_copycat_target_index(
        lineup: List[Dict[str, Any]], idx: int
    ) -> Optional[int]:
        """Resolve positional copy target index for Blueprint/Brainstorm."""
        joker = lineup[idx]
        if BalatroAlgorithm._is_blueprint_joker(joker):
            right = idx + 1
            if right < len(lineup):
                return right
            return None

        if BalatroAlgorithm._is_brainstorm_joker(joker):
            if idx == 0:
                return None
            return 0

        return None

    @staticmethod
    def _evaluate_ordered_joker_lineup(
        lineup: List[Dict[str, Any]],
        deck_state: Optional[Dict[str, Any]] = None,
        hand_cards: Optional[List[Dict[str, Any]]] = None,
        rounds: int = 3,
    ) -> float:
        """Evaluate ordered lineup with explicit copycat positional mechanics."""
        if not lineup:
            return 0.0

        memo: Dict[int, float] = {}

        def _value_at(i: int, trail: set[int]) -> float:
            if i in memo:
                return memo[i]
            if i in trail:
                return 0.0
            if not (0 <= i < len(lineup)):
                return 0.0

            joker = lineup[i]
            if BalatroAlgorithm._is_copycat_joker(joker):
                target_idx = BalatroAlgorithm._resolve_copycat_target_index(lineup, i)
                if target_idx is None:
                    value = 0.0
                else:
                    value = _value_at(target_idx, set(trail) | {i})
            else:
                value = BalatroAlgorithm._estimate_joker_projected_additive_value(
                    joker,
                    deck_state=deck_state,
                    hand_cards=hand_cards,
                    rounds=rounds,
                )

            memo[i] = float(value)
            return float(value)

        return float(sum(_value_at(i, set()) for i in range(len(lineup))))

    @staticmethod
    def auto_optimize_jokers(
        jokers: List[Dict[str, Any]],
        deck_state: Optional[Dict[str, Any]] = None,
        hand_cards: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Deterministically optimize joker order with copycat-aware insertion search.

        Uses categorical ordering for non-copycats, then jointly inserts copycats
        across all slots to avoid greedy local maxima.
        """
        if not jokers:
            return {
                "ordered_jokers": [],
                "reorder_indices": [],
                "changed": False,
                "score": 0.0,
            }

        indexed = [(idx + 1, card) for idx, card in enumerate(jokers)]
        normal = [
            row for row in indexed if not BalatroAlgorithm._is_copycat_joker(row[1])
        ]
        copycats = [
            row for row in indexed if BalatroAlgorithm._is_copycat_joker(row[1])
        ]

        normal_sorted = sorted(
            normal, key=lambda row: BalatroAlgorithm._joker_sort_key(row[1])
        )
        base_cards = [card for _, card in normal_sorted]
        base_indices = [orig for orig, _ in normal_sorted]

        if not copycats:
            changed = base_indices != [idx for idx, _ in indexed]
            return {
                "ordered_jokers": base_cards,
                "reorder_indices": base_indices,
                "changed": changed,
                "score": BalatroAlgorithm._evaluate_ordered_joker_lineup(
                    base_cards,
                    deck_state=deck_state,
                    hand_cards=hand_cards,
                    rounds=3,
                ),
            }

        has_brainstorm = any(
            BalatroAlgorithm._is_brainstorm_joker(card) for _, card in copycats
        )
        slots = list(range(len(base_cards) + 1))
        best_cards: List[Dict[str, Any]] = list(jokers)
        best_indices: List[int] = [idx for idx, _ in indexed]
        best_score = float("-inf")

        # Try all copycat orderings and all slot assignments to avoid greedy traps.
        copycat_perms = list(itertools.permutations(copycats, len(copycats)))
        if has_brainstorm and base_cards:
            base_variants: List[tuple[List[Dict[str, Any]], List[int]]] = []
            for target_idx in range(len(base_cards)):
                variant_cards = (
                    [base_cards[target_idx]]
                    + base_cards[:target_idx]
                    + base_cards[target_idx + 1 :]
                )
                variant_indices = (
                    [base_indices[target_idx]]
                    + base_indices[:target_idx]
                    + base_indices[target_idx + 1 :]
                )
                base_variants.append((variant_cards, variant_indices))
        else:
            base_variants = [(base_cards, base_indices)]

        for variant_cards, variant_indices in base_variants:
            if has_brainstorm and variant_cards:
                # Preserve a concrete non-copycat Brainstorm target at index 0.
                variant_slots = list(range(1, len(variant_cards) + 1))
            else:
                variant_slots = list(range(len(variant_cards) + 1))
            for perm in copycat_perms:
                for slot_assignment in itertools.product(
                    variant_slots, repeat=len(copycats)
                ):
                    board_cards = list(variant_cards)
                    board_indices = list(variant_indices)

                    insertion_plan = sorted(
                        zip(slot_assignment, perm),
                        key=lambda pair: pair[0],
                    )
                    offset = 0
                    for slot_idx, (orig_idx, card) in insertion_plan:
                        insert_at = int(slot_idx) + offset
                        board_cards.insert(insert_at, card)
                        board_indices.insert(insert_at, int(orig_idx))
                        offset += 1

                    score = BalatroAlgorithm._evaluate_ordered_joker_lineup(
                        board_cards,
                        deck_state=deck_state,
                        hand_cards=hand_cards,
                        rounds=3,
                    )
                    if score > best_score:
                        best_score = score
                        best_cards = board_cards
                        best_indices = board_indices

        changed = best_indices != [idx for idx, _ in indexed]
        return {
            "ordered_jokers": best_cards,
            "reorder_indices": best_indices,
            "changed": changed,
            "score": best_score,
        }

    @staticmethod
    def joker_has_scoring_effect(joker: Dict[str, Any]) -> bool:
        """Detect Jokers that directly add Chips/Mult/xMult for survival."""
        text = BalatroAlgorithm._card_text(joker)
        return (
            re.search(
                r"(x\s*mult|×\s*mult|\+\s*mult|\+\s*chips?|\bmult\b|\bchips?\b)",
                text,
            )
            is not None
        )

    @staticmethod
    def _get_card_value(card: Dict[str, Any]) -> int:
        """Get the chip value of a single card."""
        rank = card["value"]["rank"]
        if rank in ["T", "J", "Q", "K"]:
            return 10
        elif rank == "A":
            return 11
        else:
            return int(rank)

    @staticmethod
    def _evaluate_hand(
        cards: List[Dict[str, Any]],
        debuffed_rank_set: Optional[set] = None,
        debuffed_suit_set: Optional[set] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
    ) -> tuple[int, str]:
        """
        Internal method. Evaluates a specific combination of up to 5 cards.
        Debuffed cards are still included for hand-type detection but contribute
        zero chips to the score (matching Balatro's actual debuff mechanics).
        Returns a tuple: (Score Weight based on (Base Chips + Card Chips) * Base Mult, Poker Hand Name)
        """
        components = BalatroAlgorithm._evaluate_hand_components(
            cards,
            debuffed_rank_set,
            debuffed_suit_set,
            allows_four_card_hands,
            allows_gaps,
        )
        return (int(components["score"]), str(components["hand_name"]))

    @staticmethod
    def _evaluate_hand_components(
        cards: List[Dict[str, Any]],
        debuffed_rank_set: Optional[set] = None,
        debuffed_suit_set: Optional[set] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return score components so joker math can be layered on top."""
        if not cards:
            return {
                "score": 0,
                "hand_name": "High Card",
                "base_chips": 0,
                "base_mult": 1,
                "card_chips": 0,
            }

        debuffed_rank_set = debuffed_rank_set or set()
        debuffed_suit_set = debuffed_suit_set or set()

        def _is_debuffed(card: Dict[str, Any]) -> bool:
            val = card.get("value", {})
            rank = (val.get("rank") or "").upper()
            suit = (val.get("suit") or "").capitalize()
            return rank in debuffed_rank_set or suit in debuffed_suit_set

        ranks = [RANK_VALUES.get(c["value"]["rank"], 2) for c in cards]
        suits = [c["value"]["suit"] for c in cards]

        ranks.sort(reverse=True)
        rank_counts = Counter(ranks)
        counts = list(rank_counts.values())
        counts.sort(reverse=True)

        target_len = 4 if allows_four_card_hands else 5

        # Flush detection logic
        is_flush = len(cards) in (target_len, 5) and len(set(suits)) == 1

        # Straight detection logic
        is_straight = False
        if len(cards) in (target_len, 5):
            unique_ranks = sorted(list(set(ranks)), reverse=True)
            if len(unique_ranks) == len(cards):

                def _check_seq(seq):
                    for i in range(len(seq) - 1):
                        diff = seq[i] - seq[i + 1]
                        if allows_gaps:
                            if diff > 2 or diff < 1:
                                return False
                        else:
                            if diff != 1:
                                return False
                    return True

                if _check_seq(unique_ranks):
                    is_straight = True
                elif 14 in unique_ranks:
                    # Check low straight (A becoming 1)
                    low_ranks = sorted(
                        [r if r != 14 else 1 for r in unique_ranks], reverse=True
                    )
                    if _check_seq(low_ranks):
                        is_straight = True

        hand_name = "High Card"
        if is_flush and is_straight:
            if ranks[0] == 14 and ranks[1] == 13 and not allows_gaps:
                hand_name = "Royal Flush"
            else:
                hand_name = "Straight Flush"
        elif counts == [5]:
            hand_name = "Flush Five" if is_flush else "Five of a Kind"
        elif counts == [4, 1] or counts == [4]:
            hand_name = "Four of a Kind"
        elif counts == [3, 2]:
            hand_name = "Full House"
        elif is_flush:
            hand_name = "Flush"
        elif is_straight:
            hand_name = "Straight"
        elif counts == [3, 1, 1] or counts == [3]:
            hand_name = "Three of a Kind"
        elif counts == [2, 2, 1] or counts == [2, 2]:
            hand_name = "Two Pair"
        elif counts == [2, 1, 1, 1] or counts == [2]:
            hand_name = "Pair"

        base_chips, base_mult = BASE_VALUES[hand_name]
        base_chips, base_mult = BalatroAlgorithm._apply_hand_level_scaling(
            hand_name,
            float(base_chips),
            float(base_mult),
            hand_levels=hand_levels,
            planet_levels=planet_levels,
        )
        card_chips = sum(
            BalatroAlgorithm._get_card_value(c) for c in cards if not _is_debuffed(c)
        )
        score = (base_chips + card_chips) * base_mult

        return {
            "score": score,
            "hand_name": hand_name,
            "base_chips": base_chips,
            "base_mult": base_mult,
            "card_chips": card_chips,
        }

    @staticmethod
    def _apply_joker_modifiers(
        components: Dict[str, Any],
        cards: List[Dict[str, Any]],
        jokers: List[Dict[str, Any]],
        deck_name: str = "",
    ) -> int:
        """Approximate joker impact on a hand score for planning decisions."""
        base_chips = float(components.get("base_chips", 0))
        card_chips = float(components.get("card_chips", 0))
        base_mult = float(components.get("base_mult", 1))
        hand_name = str(components.get("hand_name", ""))

        flat_chips = 0.0
        flat_mult = 0.0
        x_mult = 1.0

        suits = [str((c.get("value") or {}).get("suit") or "").lower() for c in cards]
        ranks = [str((c.get("value") or {}).get("rank") or "").upper() for c in cards]
        has_face = any(r in {"J", "Q", "K"} for r in ranks)

        for joker in jokers:
            if not isinstance(joker, dict):
                continue
            effect = BalatroAlgorithm._card_text(joker)
            label = str(joker.get("label") or "").lower()

            chips_hits = BalatroAlgorithm._extract_all_numbers(
                effect, r"\+(\d+(?:\.\d+)?)\s*chips?"
            )
            mult_hits = BalatroAlgorithm._extract_all_numbers(
                effect, r"\+(\d+(?:\.\d+)?)\s*mult"
            )
            xmult_hits = BalatroAlgorithm._extract_xmult_values(effect)

            if chips_hits:
                flat_chips += sum(chips_hits)
            if mult_hits:
                flat_mult += sum(mult_hits)
            for hit in xmult_hits:
                if hit > 0:
                    x_mult *= hit

            # Lightweight conditional synergies from effect text.
            if "flush" in effect and "Flush" in hand_name:
                flat_mult += 4.0
            if "straight" in effect and "Straight" in hand_name:
                flat_mult += 4.0
            if "pair" in effect and hand_name in {
                "Pair",
                "Two Pair",
                "Three of a Kind",
                "Full House",
                "Four of a Kind",
                "Five of a Kind",
            }:
                flat_mult += 3.0
            if "face" in effect and has_face:
                x_mult *= 1.2

            spades = suits.count("spades")
            hearts = suits.count("hearts")
            if "spade" in effect and spades > 0:
                flat_mult += min(8.0, float(spades))
            if "heart" in effect and hearts > 0:
                flat_mult += min(8.0, float(hearts))

            if "chip" in label and not chips_hits:
                flat_chips += 10.0
            if "mult" in label and not mult_hits and not xmult_hits:
                flat_mult += 3.0

        # Deck-aware modifier for checkered's flush-heavy strategy.
        if deck_name.upper() == "CHECKERED" and "Flush" in hand_name:
            x_mult *= 1.1

        total_chips = max(1.0, base_chips + card_chips + flat_chips)
        total_mult = max(1.0, base_mult + flat_mult)
        return int(round(total_chips * total_mult * x_mult))

    @staticmethod
    def estimate_hand_score_for_indices(
        hand_cards: List[Dict[str, Any]],
        indices_1based: List[int],
        current_jokers: Optional[List[Dict[str, Any]]] = None,
        deck_name: str = "",
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Estimate hand score for a concrete set of selected card indices."""
        if not hand_cards or not indices_1based:
            return 0

        selected: List[Dict[str, Any]] = []
        for idx in indices_1based:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(hand_cards):
                selected.append(hand_cards[i - 1])

        if not selected:
            return 0

        debuffed_rank_set = {r.upper() for r in (debuffed_ranks or [])}
        debuffed_suit_set = {s.capitalize() for s in (debuffed_suits or [])}

        components = BalatroAlgorithm._evaluate_hand_components(
            selected,
            debuffed_rank_set,
            debuffed_suit_set,
            allows_four_card_hands,
            allows_gaps,
            hand_levels,
            planet_levels,
        )
        score = int(components.get("score", 0))
        if current_jokers:
            score = BalatroAlgorithm._apply_joker_modifiers(
                components,
                selected,
                current_jokers,
                deck_name,
            )
        return int(score)

    @staticmethod
    def find_best_hand(
        hand_cards: List[Dict[str, Any]],
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        current_jokers: Optional[List[Dict[str, Any]]] = None,
        deck_name: str = "",
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generates all combinations of all hand cards and returns the top 3 hands
        as labeled options. Debuffed cards are included in every combo — they
        still form valid hand types (Full House, Flush, etc.) but their chip
        contribution is zeroed inside _evaluate_hand.
        """
        if not hand_cards:
            return {
                "options": [
                    {"label": "A", "hand_name": "High Card", "indices": [], "score": 0}
                ]
            }

        debuffed_rank_set = {r.upper() for r in (debuffed_ranks or [])}
        debuffed_suit_set = {s.capitalize() for s in (debuffed_suits or [])}

        # All cards are candidates — debuff awareness lives in _evaluate_hand
        indexed_cards = list(enumerate(hand_cards, start=1))

        all_plays = []
        max_combo_length = min(5, len(indexed_cards))

        # Only evaluate lengths that could form significant hands to save compute
        # but since max len is 5, combinations are cheap, just do 1 to max
        for r in range(1, max_combo_length + 1):
            for combo in itertools.combinations(indexed_cards, r):
                indices = [idx for idx, _ in combo]
                cards_only = [card for _, card in combo]
                components = BalatroAlgorithm._evaluate_hand_components(
                    cards_only,
                    debuffed_rank_set,
                    debuffed_suit_set,
                    allows_four_card_hands,
                    allows_gaps,
                    hand_levels,
                    planet_levels,
                )
                score = int(components.get("score", 0))
                name = str(components.get("hand_name", "High Card"))

                if current_jokers:
                    score = BalatroAlgorithm._apply_joker_modifiers(
                        components,
                        cards_only,
                        current_jokers,
                        deck_name,
                    )

                all_plays.append(
                    {"hand_name": name, "indices": indices, "score": score}
                )

        # Sort by highest score, then by most cards played
        all_plays.sort(key=lambda x: (x["score"], len(x["indices"])), reverse=True)

        # Deduplicate identical index combinations and grab top 3
        seen_combos = set()
        top_options = []
        labels = ["A", "B", "C"]

        for play in all_plays:
            combo_sig = tuple(sorted(play["indices"]))
            if combo_sig not in seen_combos:
                seen_combos.add(combo_sig)
                play["label"] = labels[len(top_options)]
                top_options.append(play)
                if len(top_options) == 3:
                    break

        return {"options": top_options}

    @staticmethod
    def _estimate_hand_profile_from_options(
        hand_cards: Optional[List[Dict[str, Any]]],
        current_jokers: Optional[List[Dict[str, Any]]] = None,
        deck_name: str = "",
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """Estimate probabilities for hand-shape conditions from top play options."""
        cards = [card for card in (hand_cards or []) if isinstance(card, dict)]
        if not cards:
            return {
                "prob_small_hand": 0.0,
                "prob_flush_like": 0.0,
                "prob_straight_like": 0.0,
                "prob_pair_family": 0.0,
            }

        result = BalatroAlgorithm.find_best_hand(
            cards,
            current_jokers=current_jokers,
            deck_name=deck_name,
            hand_levels=hand_levels,
            planet_levels=planet_levels,
        )
        options = result.get("options", [])
        if not options:
            return {
                "prob_small_hand": 0.0,
                "prob_flush_like": 0.0,
                "prob_straight_like": 0.0,
                "prob_pair_family": 0.0,
            }

        weights = [max(1.0, float(opt.get("score", 0) or 0)) for opt in options]
        denom = sum(weights)
        if denom <= 0:
            denom = float(len(weights) or 1)
            weights = [1.0 for _ in options]

        pair_family_names = {
            "Pair",
            "Two Pair",
            "Three of a Kind",
            "Full House",
            "Four of a Kind",
            "Five of a Kind",
        }
        prob_small_hand = 0.0
        prob_flush_like = 0.0
        prob_straight_like = 0.0
        prob_pair_family = 0.0

        for opt, weight in zip(options, weights):
            p = weight / denom
            indices = opt.get("indices", [])
            hand_name = str(opt.get("hand_name", ""))
            if isinstance(indices, list) and len(indices) <= 4:
                prob_small_hand += p
            if "Flush" in hand_name:
                prob_flush_like += p
            if "Straight" in hand_name:
                prob_straight_like += p
            if hand_name in pair_family_names:
                prob_pair_family += p

        return {
            "prob_small_hand": max(0.0, min(1.0, prob_small_hand)),
            "prob_flush_like": max(0.0, min(1.0, prob_flush_like)),
            "prob_straight_like": max(0.0, min(1.0, prob_straight_like)),
            "prob_pair_family": max(0.0, min(1.0, prob_pair_family)),
        }

    @staticmethod
    def score_joker_value(
        joker: Dict[str, Any],
        current_jokers: List[Dict[str, Any]],
        hand_stats: Dict[str, Any],
        deck_name: str = "",
        hand_cards: Optional[List[Dict[str, Any]]] = None,
        deck_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Heuristic advisor label for a shop joker to guide LLM purchase decisions.

        The LLM retains full authority — this label is advisory only and is
        appended to the shop item JSON as an 'advisor' field.

        Args:
            joker: The shop card dict (CardItem shape from BalatroState).
            current_jokers: List of currently equipped joker dicts.
            hand_stats: Dict with keys 'total_chips' (int) and 'joker_count' (int).
            deck_name: Optional deck name for deck-specific scoring.

        Returns:
            One of: 'HIGH SYNERGY', 'MODERATE VALUE', 'LOW VALUE', 'SITUATIONAL', 'BAD SYNERGY'.
        """
        label = (joker.get("label") or "").lower()
        effect = BalatroAlgorithm._card_text(joker)
        total_chips: int = hand_stats.get("total_chips", 0)
        joker_count: int = hand_stats.get("joker_count", 0)
        deck_upper = deck_name.upper() if deck_name else ""
        key = str(joker.get("key") or "").lower()

        # Hard override: user-specified denylist jokers should always be skipped.
        if key in FORCED_BAD_SYNERGY_JOKER_KEYS:
            return "BAD SYNERGY"

        # Any scaler still at "Currently +0 Chips/Mult" is treated as dead value.
        if re.search(r"currently\s*\+\s*0\s*(chips|mult|x?mult)", effect, re.IGNORECASE):
            return "BAD SYNERGY"

        # Checkered strategy is deterministic flush-first; avoid high-variance jokers.
        if deck_upper == "CHECKERED" and BalatroAlgorithm._is_unreliable_joker_effect(effect):
            return "BAD SYNERGY"

        state_hand_levels, state_planet_levels = (
            BalatroAlgorithm._extract_explicit_level_maps(deck_state)
        )
        hand_profile = BalatroAlgorithm._estimate_hand_profile_from_options(
            hand_cards,
            current_jokers=None,
            deck_name=deck_name,
            hand_levels=state_hand_levels,
            planet_levels=state_planet_levels,
        )

        # Detect already-owned joker (simple duplicate check by key).
        owned_keys = {j.get("key") for j in current_jokers if isinstance(j, dict)}
        if joker.get("key") in owned_keys:
            return "LOW VALUE"  # Duplicate — most jokers don't stack.

        # At Ante 5+ a flat +Mult joker is LOW VALUE when xMult already exists on
        # the board — the multiplicative engine makes additive Mult marginal.
        try:
            ante = int((deck_state or {}).get("ante_num") or 1)
        except (TypeError, ValueError):
            ante = 1
        has_flat_mult_hits = bool(
            BalatroAlgorithm._extract_all_numbers(
                effect, r"\+(\d+(?:\.\d+)?)\s*mult"
            )
        )
        flat_mult_only = (
            has_flat_mult_hits
            and not BalatroAlgorithm._extract_xmult_values(effect)
        )
        if ante >= 5 and flat_mult_only:
            lineup_has_xmult = any(
                bool(
                    BalatroAlgorithm._extract_xmult_values(
                        BalatroAlgorithm._card_text(j)
                    )
                )
                for j in current_jokers
                if isinstance(j, dict)
            )
            if lineup_has_xmult:
                return "LOW VALUE"


        has_tarot_run_scaling_condition = (
            "tarot" in effect
            and re.search(r"per\s+tarot", effect, re.IGNORECASE) is not None
            and (
                "used this run" in effect
                or "this run" in effect
                or "tarot cards used" in effect
            )
        )
        has_exact_four_hand_condition = (
            re.search(r"exactly\s*4\s*cards?", effect, re.IGNORECASE) is not None
            or re.search(r"\b4\s*-?card\b", effect, re.IGNORECASE) is not None
        )

        if has_tarot_run_scaling_condition:
            tarot_visible = BalatroAlgorithm._count_visible_tarot_cards(deck_state)
            pack_count = len(BalatroAlgorithm._state_cards(deck_state, "packs"))
            money_now = 0
            if isinstance(deck_state, dict):
                try:
                    money_now = int(deck_state.get("money") or 0)
                except (TypeError, ValueError):
                    money_now = 0

            tarot_support_score = 0
            if tarot_visible > 0:
                tarot_support_score += 2
            if pack_count > 0:
                tarot_support_score += 1
            if money_now >= 10:
                tarot_support_score += 1

            if tarot_support_score <= 0:
                return "BAD SYNERGY" if deck_upper == "CHECKERED" else "LOW VALUE"
            if tarot_support_score == 1:
                return "LOW VALUE"
            if tarot_support_score >= 3:
                return "HIGH SYNERGY"
            return "MODERATE VALUE"

        if deck_upper == "CHECKERED":
            # In Checkered (Flush-focused) runs, these mechanics are usually dead weight.
            has_suit_mismatch = "diamond" in effect or "club" in effect
            has_small_hand_condition = (
                "half joker" in label
                or "half joker" in effect
                or re.search(
                    r"(3|4|three|four)\s*(or|and)\s*(fewer|less)",
                    effect,
                    re.IGNORECASE,
                )
                is not None
                or (
                    "played hand" in effect
                    and re.search(
                        r"\b(3|4|three|four)\s*-?card\b", effect, re.IGNORECASE
                    )
                    is not None
                )
                or re.search(r"<=\s*4", effect, re.IGNORECASE) is not None
            )

            # Square Joker and exactly-4-card hand conditions are anti-synergy in
            # flush-first Checkered runs unless small hands are highly probable.
            if has_exact_four_hand_condition:
                p_small = float(hand_profile.get("prob_small_hand", 0.0))
                if p_small < 0.55:
                    return "BAD SYNERGY"
                if p_small < 0.7:
                    return "LOW VALUE"

            has_straight_only_condition = (
                "straight" in effect
                and "straight flush" not in effect
                and "flush" not in effect
            )
            has_pair_only_condition = (
                any(
                    token in effect
                    for token in ("pair", "two pair", "three of a kind", "full house")
                )
                and "flush" not in effect
                and "straight" not in effect
            )
            if has_suit_mismatch:
                return "BAD SYNERGY"

            if has_small_hand_condition:
                p_small = float(hand_profile.get("prob_small_hand", 0.0))
                if p_small < 0.25:
                    return "BAD SYNERGY"
                if p_small < 0.45:
                    return "LOW VALUE"

            if has_straight_only_condition:
                p_straight = float(hand_profile.get("prob_straight_like", 0.0))
                if p_straight < 0.25:
                    return "BAD SYNERGY"
                if p_straight < 0.5:
                    return "LOW VALUE"

            if has_pair_only_condition:
                p_pair = float(hand_profile.get("prob_pair_family", 0.0))
                if p_pair < 0.25:
                    return "BAD SYNERGY"
                if p_pair < 0.5:
                    return "LOW VALUE"

        if hand_cards:
            impact = BalatroAlgorithm.evaluate_shop_joker_impact(
                joker,
                current_jokers,
                hand_cards,
                deck_name=deck_name,
                deck_state=deck_state,
            )
            delta_pct = float(impact.get("delta_pct", 0.0))
            if delta_pct >= 25.0:
                return "HIGH SYNERGY"
            if delta_pct >= 8.0:
                return "MODERATE VALUE"
            if delta_pct <= 0.0:
                return "LOW VALUE"

        scaling_bonus = BalatroAlgorithm._estimate_scaling_joker_bonus(
            joker,
            current_jokers,
            deck_state=deck_state,
            deck_name=deck_name,
            hand_cards=hand_cards,
        )
        if scaling_bonus >= 30.0:
            return "HIGH SYNERGY"
        if scaling_bonus >= 16.0:
            return "MODERATE VALUE"

        if deck_upper == "CHECKERED":
            # Prevent buying jokers with negative synergy for Checkered Deck
            if "diamond" in effect or "club" in effect:
                return "BAD SYNERGY"
            # Suit-specific jokers (Hearts/Spades) are priority
            if "heart" in effect or "spade" in effect:
                return "HIGH SYNERGY"
            # Jupiter scales with Flush hands
            if "jupiter" in label:
                return "HIGH SYNERGY"

        # xMult jokers are the highest-value scaling tool in Balatro.
        if "xmult" in effect or BalatroAlgorithm._extract_xmult_values(effect):
            return "HIGH SYNERGY"

        # Flat +Chips is extremely valuable when chips are still low.
        if "+chips" in effect or "chips" in effect:
            if total_chips < 100:
                return "HIGH SYNERGY"
            return "MODERATE VALUE"

        # Flat +Mult is good early when joker slots are not full.
        if "+mult" in effect or "mult" in effect:
            if joker_count < 3:
                return "MODERATE VALUE"
            return "SITUATIONAL"

        # Economy jokers (money-generating) are valuable mid-game.
        if "money" in effect or "interest" in effect or "gold" in label:
            return "MODERATE VALUE"

        # Retrigger and retrieval jokers are situationally powerful.
        if "retrigger" in effect or "retrigg" in effect:
            return "SITUATIONAL"

        return "SITUATIONAL"

    @staticmethod
    def score_voucher_value(voucher: Dict[str, Any], money: int, ante: int) -> str:
        """Heuristic advisor label for vouchers."""
        text = BalatroAlgorithm._card_text(voucher)
        label = str(voucher.get("label") or "").lower()

        if "overstock" in label or "clearance" in text:
            return "HIGH SYNERGY"
        if "reroll" in text and money >= 20:
            return "MODERATE VALUE"
        if "interest" in text and ante <= 2:
            return "MODERATE VALUE"
        if "blank" in label:
            return "LOW VALUE"
        if money <= 6:
            return "LOW VALUE"
        return "SITUATIONAL"

    @staticmethod
    def estimate_best_score(
        hand_cards: List[Dict[str, Any]],
        current_jokers: Optional[List[Dict[str, Any]]] = None,
        deck_name: str = "",
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Estimate best available score for current hand and joker lineup."""
        result = BalatroAlgorithm.find_best_hand(
            hand_cards,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
            current_jokers=current_jokers,
            deck_name=deck_name,
            hand_levels=hand_levels,
            planet_levels=planet_levels,
        )
        options = result.get("options", [])
        if not options:
            return 0
        return int(options[0].get("score", 0))

    @staticmethod
    def evaluate_shop_joker_impact(
        shop_card: Dict[str, Any],
        current_jokers: List[Dict[str, Any]],
        hand_cards: List[Dict[str, Any]],
        deck_name: str = "",
        deck_state: Optional[Dict[str, Any]] = None,
        joker_slot_limit: int = 5,
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        hand_levels: Optional[Dict[str, Any]] = None,
        planet_levels: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Estimate score delta of buying a shop joker (with replacement if full)."""
        state_hand_levels, state_planet_levels = (
            BalatroAlgorithm._extract_explicit_level_maps(deck_state)
        )
        if hand_levels is None:
            hand_levels = state_hand_levels
        if planet_levels is None:
            planet_levels = state_planet_levels

        baseline = BalatroAlgorithm.estimate_best_score(
            hand_cards,
            current_jokers=current_jokers,
            deck_name=deck_name,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
            hand_levels=hand_levels,
            planet_levels=planet_levels,
        )
        baseline += BalatroAlgorithm._lineup_strategic_bonus(
            current_jokers,
            deck_state=deck_state,
            deck_name=deck_name,
            hand_cards=hand_cards,
        )

        if not BalatroAlgorithm.is_probable_joker(shop_card):
            return {
                "baseline_score": baseline,
                "projected_score": baseline,
                "delta": 0,
                "delta_pct": 0.0,
                "expected_score": baseline,
                "expected_delta": 0,
                "expected_delta_pct": 0.0,
                "immediate_impact": 0,
                "projected_3_round_impact": 0,
                "replacement_index": None,
            }

        candidates: List[tuple[Optional[int], List[Dict[str, Any]]]] = []

        if len(current_jokers) < joker_slot_limit:
            candidates.append((None, current_jokers + [shop_card]))
        else:
            for idx in range(len(current_jokers)):
                candidate = list(current_jokers)
                candidate[idx] = shop_card
                candidates.append((idx + 1, candidate))

        best_score = baseline
        best_replace: Optional[int] = None
        for replace_idx, lineup in candidates:
            score = BalatroAlgorithm.estimate_best_score(
                hand_cards,
                current_jokers=lineup,
                deck_name=deck_name,
                debuffed_ranks=debuffed_ranks,
                debuffed_suits=debuffed_suits,
                allows_four_card_hands=allows_four_card_hands,
                allows_gaps=allows_gaps,
                hand_levels=hand_levels,
                planet_levels=planet_levels,
            )
            score += BalatroAlgorithm._lineup_strategic_bonus(
                lineup,
                deck_state=deck_state,
                deck_name=deck_name,
                hand_cards=hand_cards,
            )
            if score > best_score:
                best_score = score
                best_replace = replace_idx

        delta = best_score - baseline
        delta_pct = (delta / max(1, baseline)) * 100.0

        baseline_expected = BalatroAlgorithm.estimate_lineup_expected_score(
            hand_cards,
            current_jokers,
            deck_name=deck_name,
            deck_state=deck_state,
            rounds=3,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
            hand_levels=hand_levels,
            planet_levels=planet_levels,
        )
        projected_expected = baseline_expected
        for _replace_idx, lineup in candidates:
            lineup_expected = BalatroAlgorithm.estimate_lineup_expected_score(
                hand_cards,
                lineup,
                deck_name=deck_name,
                deck_state=deck_state,
                rounds=3,
                debuffed_ranks=debuffed_ranks,
                debuffed_suits=debuffed_suits,
                allows_four_card_hands=allows_four_card_hands,
                allows_gaps=allows_gaps,
                hand_levels=hand_levels,
                planet_levels=planet_levels,
            )
            if lineup_expected > projected_expected:
                projected_expected = lineup_expected

        expected_delta = projected_expected - baseline_expected
        expected_delta_pct = (expected_delta / max(1, baseline_expected)) * 100.0
        immediate_impact = int(
            round(
                BalatroAlgorithm._estimate_joker_immediate_additive_value(
                    shop_card,
                    baseline_score=float(baseline),
                )
            )
        )
        projected_3_round_impact = int(
            round(
                BalatroAlgorithm._estimate_joker_projected_additive_value(
                    shop_card,
                    deck_state=deck_state,
                    hand_cards=hand_cards,
                    rounds=3,
                )
            )
        )
        return {
            "baseline_score": baseline,
            "projected_score": best_score,
            "delta": delta,
            "delta_pct": round(delta_pct, 2),
            "expected_score": projected_expected,
            "expected_delta": expected_delta,
            "expected_delta_pct": round(expected_delta_pct, 2),
            "immediate_impact": immediate_impact,
            "projected_3_round_impact": projected_3_round_impact,
            "replacement_index": best_replace,
        }

    @staticmethod
    def identify_safe_sell_jokers(
        current_jokers: List[Dict[str, Any]],
        hand_cards: List[Dict[str, Any]],
        deck_name: str = "",
        tolerance_pct: float = 2.0,
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        current_money: Optional[int] = None,
        max_jokers: int = 5,
        deck_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Return jokers whose removal has minimal score impact."""
        if not current_jokers:
            return []

        baseline = BalatroAlgorithm.estimate_best_score(
            hand_cards,
            current_jokers=current_jokers,
            deck_name=deck_name,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
        )
        baseline_projected = BalatroAlgorithm.estimate_lineup_expected_score(
            hand_cards,
            current_jokers,
            deck_name=deck_name,
            deck_state=deck_state,
            rounds=3,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
        )
        combined_baseline = max(1, baseline) + max(1, baseline_projected)

        safe: List[Dict[str, Any]] = []
        at_capacity = len(current_jokers) >= max(1, int(max_jokers or 5))
        below_interest_floor = (
            bool(current_money is not None) and int(current_money) < 25
        )
        hand_stats = {
            "total_chips": 0,
            "joker_count": len(current_jokers),
            "hand_count": len(hand_cards),
        }

        for idx in range(len(current_jokers)):
            trimmed = [j for i, j in enumerate(current_jokers) if i != idx]
            trimmed_score = BalatroAlgorithm.estimate_best_score(
                hand_cards,
                current_jokers=trimmed,
                deck_name=deck_name,
                debuffed_ranks=debuffed_ranks,
                debuffed_suits=debuffed_suits,
                allows_four_card_hands=allows_four_card_hands,
                allows_gaps=allows_gaps,
            )
            trimmed_projected = BalatroAlgorithm.estimate_lineup_expected_score(
                hand_cards,
                trimmed,
                deck_name=deck_name,
                deck_state=deck_state,
                rounds=3,
                debuffed_ranks=debuffed_ranks,
                debuffed_suits=debuffed_suits,
                allows_four_card_hands=allows_four_card_hands,
                allows_gaps=allows_gaps,
            )
            immediate_loss_pct = ((baseline - trimmed_score) / max(1, baseline)) * 100.0
            projected_loss_pct = (
                (baseline_projected - trimmed_projected) / max(1, baseline_projected)
            ) * 100.0
            immediate_loss_abs = int(max(0, baseline - trimmed_score))
            projected_loss_abs = int(max(0, baseline_projected - trimmed_projected))
            combined_loss_abs = immediate_loss_abs + projected_loss_abs
            combined_loss_pct = (
                combined_loss_abs / max(1.0, float(combined_baseline))
            ) * 100.0
            joker = current_jokers[idx]

            advisor_label = ""
            try:
                advisor_label = BalatroAlgorithm.score_joker_value(
                    joker,
                    trimmed,
                    hand_stats,
                    deck_name=deck_name,
                    hand_cards=hand_cards,
                )
            except Exception:
                advisor_label = ""

            if advisor_label == "BAD SYNERGY" and (at_capacity or below_interest_floor):
                safe.append(
                    {
                        "index": idx + 1,
                        "label": joker.get("label")
                        or joker.get("key")
                        or f"Joker {idx + 1}",
                        "loss_pct": round(immediate_loss_pct, 2),
                        "loss_abs": immediate_loss_abs,
                        "projected_loss_pct": round(projected_loss_pct, 2),
                        "projected_loss_abs": projected_loss_abs,
                        "combined_loss_abs": combined_loss_abs,
                        "combined_loss_pct": round(combined_loss_pct, 2),
                        "reason": "BAD SYNERGY",
                        "advisor": advisor_label,
                    }
                )
                continue

            tolerance_abs = (max(0.0, float(tolerance_pct)) / 100.0) * float(
                combined_baseline
            )
            if float(combined_loss_abs) <= tolerance_abs:
                safe.append(
                    {
                        "index": idx + 1,
                        "label": joker.get("label")
                        or joker.get("key")
                        or f"Joker {idx + 1}",
                        "loss_pct": round(immediate_loss_pct, 2),
                        "loss_abs": immediate_loss_abs,
                        "projected_loss_pct": round(projected_loss_pct, 2),
                        "projected_loss_abs": projected_loss_abs,
                        "combined_loss_abs": combined_loss_abs,
                        "combined_loss_pct": round(combined_loss_pct, 2),
                        "reason": "LOW IMPACT",
                        "advisor": advisor_label,
                    }
                )

        safe.sort(
            key=lambda row: (
                row.get("combined_loss_abs", 0),
                row.get("loss_pct", 0.0),
            )
        )
        return safe

    @staticmethod
    def find_best_discard(
        hand_cards: List[Dict[str, Any]],
        best_play_indices: List[int],
        target_hand_type: Optional[str] = None,
    ) -> List[int]:
        """
        Discards up to 5 cards to improve hand potential.
        Prioritises completing draws (flushes, straights) over discarding low cards.
        Returns 1-based indices as expected by the Lua API.

        Args:
            target_hand_type: When supplied and it contains "Flush" (e.g. "Flush",
                "Straight Flush", "Flush Five"), the function operates in
                *flush-committed mode*: it skips straight-draw detection entirely
                and discards every card that does not match the dominant flush suit
                found in the current hand.  If no dominant suit (count >= 4) exists
                the parameter is ignored and normal logic runs.
        """
        # Get indices and cards that are not part of the best play
        unused = [
            (idx, card)
            for idx, card in enumerate(hand_cards, start=1)
            if idx not in best_play_indices
        ]

        if not unused:
            return []

        # 1. Detect dominant flush suit (consider full hand, not only unused)
        all_suits = [card["value"]["suit"] for card in hand_cards]
        suit_counts = Counter(all_suits)
        flush_suit = None
        for suit, count in suit_counts.items():
            if count >= 4:
                flush_suit = suit
                break

        # 1a. Flush-committed mode: skip straight-draw logic entirely.
        #     Only entered when the caller has already committed to a flush-family
        #     hand type AND the current hand actually has a dominant suit to build on.
        if target_hand_type and "Flush" in target_hand_type and flush_suit:
            discard_candidates = [
                (idx, card)
                for idx, card in enumerate(hand_cards, start=1)
                if card["value"]["suit"] != flush_suit and idx not in best_play_indices
            ]
            discard_combo = discard_candidates[:5]
            if discard_combo:
                return [idx for idx, _ in discard_combo]
            # No off-suit cards to discard — fall through to normal logic.

        # 1b. Generic 4-card flush draw (no committed hand type).
        if flush_suit and not (target_hand_type and "Flush" in target_hand_type):
            # Discard cards not of the flush suit, preferring to keep the best play indices
            discard_candidates = [
                (idx, card)
                for idx, card in enumerate(hand_cards, start=1)
                if card["value"]["suit"] != flush_suit and idx not in best_play_indices
            ]
            discard_combo = discard_candidates[:5]
            if discard_combo:
                return [idx for idx, _ in discard_combo]

        # 2. Check for 4-card open-ended straight draw (8 outs only)
        ranks_with_indices = [
            (idx, RANK_VALUES.get(card["value"]["rank"], 2))
            for idx, card in enumerate(hand_cards, start=1)
        ]

        unique_ranks_desc = sorted(
            {rank for _, rank in ranks_with_indices}, reverse=True
        )
        open_window = None
        for i in range(len(unique_ranks_desc) - 3):
            window = unique_ranks_desc[i : i + 4]
            if window[0] - window[3] == 3 and window[0] < 14:
                open_window = window
                break

        straight_protected = set()
        if open_window:
            seen_ranks = set()
            for idx, rank in ranks_with_indices:
                if rank in open_window and rank not in seen_ranks:
                    seen_ranks.add(rank)
                    straight_protected.add(idx)
                    if len(seen_ranks) == 4:
                        break

        # 3. Fallback to discarding lowest ranked unused cards, but protect high cards.
        unused_with_values = [
            (idx, card, RANK_VALUES.get(card["value"]["rank"], 2))
            for idx, card in unused
        ]

        # Sort by value ascending, then by index to keep consistent discard order
        unused_with_values.sort(key=lambda x: (x[2], x[0]))

        # Protect open-ended straight draw cards and high cards (Q or better)
        protected_indices = set(straight_protected)
        high_card_indices = set()
        for idx, card, value in unused_with_values:
            if value >= 12:  # Queen or better
                high_card_indices.add(idx)

        if len(high_card_indices) <= 3:
            protected_indices.update(high_card_indices)

        # Discard the unprotected low cards first, limiting to 5 total discards
        discard_combo = [
            (idx, card) for idx, card in unused if idx not in protected_indices
        ][:5]

        # Return only the genuinely unprotected cards — never force the quota
        # to 5 by raiding protected high cards. Discarding fewer than 5 is fine.
        return [idx for idx, _ in discard_combo]

    @staticmethod
    def evaluate_consumable_plays(
        consumables: List[Dict[str, Any]],
        hand_cards: List[Dict[str, Any]],
        deck_name: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Detects highly beneficial Tarot/Spectral plays and returns them as Guided Options.
        """
        scored_options: List[Dict[str, Any]] = []
        labels = ["C1", "C2", "C3", "C4"]
        deck_upper = deck_name.upper() if deck_name else ""

        if not consumables:
            return []

        # Map ranks to values to find high/low cards for targets
        indexed_hand = []
        if hand_cards:
            indexed_hand = [
                (i, c, RANK_VALUES.get(c.get("value", {}).get("rank", "2"), 2))
                for i, c in enumerate(hand_cards, start=1)
            ]
            indexed_hand.sort(key=lambda x: x[2])  # Sort by rank ascending

        for c_idx, consumable in enumerate(consumables, start=1):
            name = (consumable.get("label") or "").lower()
            key = (consumable.get("key") or "").lower()

            # Consumables (No targets needed)
            no_target_names = [
                "hermit",
                "temperance",
                "fool",
                "high priestess",
                "emperor",
                "wheel of fortune",
                "judgement",
                "pluto",
                "mercury",
                "uranus",
                "venus",
                "saturn",
                "jupiter",
                "earth",
                "mars",
                "neptune",
                "planet x",
                "ceres",
                "eris",
                "aura",
                "wraith",
                "ouija",
                "ectoplasm",
                "familiar",
                "incantation",
                "talisman",
                "seance",
                "immolate",
                "ankh",
                "cryptid",
                "grim",
            ]

            is_planet = (
                any(
                    nt in name
                    for nt in [
                        "pluto",
                        "mercury",
                        "uranus",
                        "venus",
                        "saturn",
                        " earth",
                        "mars",
                        "neptune",
                        "planet x",
                        "ceres",
                        "eris",
                        "jupiter",
                    ]
                )
                or "planet" in key
                or key.startswith("c_planet")
                or name.startswith("planet")
            )

            if any(nt in name for nt in no_target_names) or is_planet:
                priority = 60.0
                reasoning = f"Use {name.capitalize()} for immediate value."

                if "jupiter" in name and deck_upper == "CHECKERED":
                    priority = 95.0
                    reasoning = (
                        "Use Jupiter now to level Flush for Checkered consistency."
                    )
                elif is_planet:
                    priority = 72.0
                    reasoning = f"Use {name.capitalize()} to scale hand levels."
                elif any(econ in name for econ in ["hermit", "temperance", "fool"]):
                    priority = 78.0
                    reasoning = f"Use {name.capitalize()} for economy/snowball value."

                scored_options.append(
                    {
                        "priority": priority,
                        "action": "use_consumable",
                        "indices": [c_idx],
                        "reasoning": reasoning,
                    }
                )
                continue

            # Target-based cards (Requires hand_cards)
            if not indexed_hand:
                continue

            # Death splits: target lowest to copy highest
            if "death" in name and len(indexed_hand) >= 2:
                lowest_idx = indexed_hand[0][0]
                highest_idx = indexed_hand[-1][0]
                scored_options.append(
                    {
                        "priority": 68.0,
                        "action": "use_consumable",
                        "indices": [c_idx, lowest_idx, highest_idx],
                        "reasoning": f"Use Death on lowest card ({lowest_idx}) to copy highest card ({highest_idx}).",
                    }
                )

            # Destructors (Hanged Man, Tower): target lowest cards
            elif (
                any(x in name for x in ["hanged man", "tower"])
                and len(indexed_hand) >= 1
            ):
                target_count = 2 if "hanged man" in name else 1
                targets = [card[0] for card in indexed_hand[:target_count]]
                scored_options.append(
                    {
                        "priority": 64.0,
                        "action": "use_consumable",
                        "indices": [c_idx] + targets,
                        "reasoning": f"Use {name.capitalize()} to destroy lowest cards: {targets}.",
                    }
                )

            # Modifiers/Spectrals that destroy (Hex): target lowest card for positive value
            elif "hex" in name and len(indexed_hand) >= 1:
                targets = [indexed_hand[0][0]]
                scored_options.append(
                    {
                        "priority": 70.0,
                        "action": "use_consumable",
                        "indices": [c_idx] + targets,
                        "reasoning": f"Use {name.capitalize()} on lowest card ({targets[0]}) to gain Polychrome.",
                    }
                )

            # Enhancers (Strength, Empress, Hierophant, Justice, Magician, Devil): target highest cards
            elif (
                any(
                    x in name
                    for x in [
                        "strength",
                        "empress",
                        "hierophant",
                        "justice",
                        "magician",
                        "devil",
                    ]
                )
                and len(indexed_hand) >= 1
            ):
                target_count = 1 if name in ["justice", "devil"] else 2
                targets = [card[0] for card in indexed_hand[-target_count:]]
                scored_options.append(
                    {
                        "priority": 66.0,
                        "action": "use_consumable",
                        "indices": [c_idx] + targets,
                        "reasoning": f"Use {name.capitalize()} to enhance your best cards: {targets}.",
                    }
                )

            # Suit changers / Misc (Sun, Moon, Star, World, Lovers): target highest cards
            elif (
                any(x in name for x in ["sun", "moon", "star", "world", "lovers"])
                and len(indexed_hand) >= 1
            ):
                target_count = 3 if name in ["sun", "moon", "star", "world"] else 2
                targets = [card[0] for card in indexed_hand[-target_count:]]
                scored_options.append(
                    {
                        "priority": 65.0,
                        "action": "use_consumable",
                        "indices": [c_idx] + targets,
                        "reasoning": f"Use {name.capitalize()} on best cards to convert suits: {targets}.",
                    }
                )

        scored_options.sort(
            key=lambda row: float(row.get("priority", 0.0)), reverse=True
        )
        top = scored_options[:4]
        options: List[Dict[str, Any]] = []
        for idx, option in enumerate(top):
            options.append(
                {
                    "label": labels[idx] if idx < len(labels) else f"C{idx + 1}",
                    "action": option.get("action", "use_consumable"),
                    "indices": option.get("indices", []),
                    "reasoning": str(
                        option.get("reasoning") or "Use consumable for value."
                    ),
                }
            )

        return options

    @staticmethod
    def evaluate_pack_options(
        pack_cards: List[Dict[str, Any]],
        current_jokers: List[Dict[str, Any]],
        hand_stats: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Pre-labels pack choices. If it's a Joker pack, evaluates synergy.
        """
        options = []
        labels = ["P1", "P2", "P3", "P4", "P5"]

        probable_buffoon_pack = (
            sum(1 for card in pack_cards if BalatroAlgorithm.is_probable_joker(card))
            >= 2
        )

        for i, card in enumerate(pack_cards, start=1):
            name = card.get("label", "Unknown")
            reasoning = f"Select {name}."

            # If it's a Joker, run it through our scoring heuristic
            if "Joker" in card.get("key", "") or card.get("value", {}).get("effect"):
                try:
                    advisor_label = BalatroAlgorithm.score_joker_value(
                        card, current_jokers, hand_stats
                    )
                    reasoning = f"Select {name} (Advisor Rating: {advisor_label})."
                except Exception:
                    pass

            if probable_buffoon_pack and BalatroAlgorithm.is_probable_joker(card):
                if "Advisor Rating:" in reasoning:
                    reasoning = reasoning.replace(
                        "Advisor Rating:",
                        "HIGH PRIORITY: Buffoon pick | Advisor Rating:",
                    )
                else:
                    reasoning = f"HIGH PRIORITY: Buffoon pick - {reasoning}"

            options.append(
                {
                    "label": labels[i - 1] if (i - 1) < len(labels) else f"P{i}",
                    "action": "choose_pack",
                    "indices": [i],
                    "reasoning": reasoning,
                }
            )

        return options
