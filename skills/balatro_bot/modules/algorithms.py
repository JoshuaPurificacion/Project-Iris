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
    def _state_cards(raw_state: Optional[Dict[str, Any]], container_name: str) -> List[Dict[str, Any]]:
        """Safely read a card list from a Balatro state container."""
        if not isinstance(raw_state, dict):
            return []

        container = raw_state.get(container_name) or {}
        if not isinstance(container, dict):
            return []

        cards = container.get("cards", [])
        if not isinstance(cards, list):
            return []

        return [card for card in cards if isinstance(card, dict)]

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
            visible_cards = [card for card in (hand_cards or []) if isinstance(card, dict)]

        enhancements = [
            BalatroAlgorithm._card_enhancement(card)
            for card in visible_cards
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
            "has_magic_trick": ("magic trick" in voucher_text) or ("magic_trick" in voucher_text),
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
        key = str(joker.get("key") or "").lower()
        label = str(joker.get("label") or "").lower()
        state = BalatroAlgorithm._collect_scaling_state(deck_state, hand_cards, current_jokers)

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
            xmult_hits = BalatroAlgorithm._extract_all_numbers(
                effect, r"(?:x|×)\s*(\d+(?:\.\d+)?)\s*mult"
            )

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
    def find_best_hand(
        hand_cards: List[Dict[str, Any]],
        debuffed_ranks: Optional[List[str]] = None,
        debuffed_suits: Optional[List[str]] = None,
        allows_four_card_hands: bool = False,
        allows_gaps: bool = False,
        current_jokers: Optional[List[Dict[str, Any]]] = None,
        deck_name: str = "",
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
            One of: 'HIGH SYNERGY', 'MODERATE VALUE', 'LOW VALUE', 'SITUATIONAL'.
        """
        label = (joker.get("label") or "").lower()
        effect = BalatroAlgorithm._card_text(joker)
        total_chips: int = hand_stats.get("total_chips", 0)
        joker_count: int = hand_stats.get("joker_count", 0)

        # Detect already-owned joker (simple duplicate check by key).
        owned_keys = {j.get("key") for j in current_jokers if isinstance(j, dict)}
        if joker.get("key") in owned_keys:
            return "LOW VALUE"  # Duplicate — most jokers don't stack.

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

        # Deck-specific priority scoring
        deck_upper = deck_name.upper() if deck_name else ""

        if deck_upper == "ABANDONED":
            # Ride the Bus is top priority - scales +Mult per hand played
            if "ride" in label and "bus" in label:
                return "HIGH SYNERGY"

        if deck_upper == "CHECKERED":
            # Suit-specific jokers (Hearts/Spades) are priority
            if "heart" in effect or "spade" in effect:
                return "HIGH SYNERGY"
            # Jupiter scales with Flush hands
            if "jupiter" in label:
                return "HIGH SYNERGY"

        # xMult jokers are the highest-value scaling tool in Balatro.
        if "xmult" in effect or "x mult" in effect or "×" in effect:
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
    ) -> Dict[str, Any]:
        """Estimate score delta of buying a shop joker (with replacement if full)."""
        baseline = BalatroAlgorithm.estimate_best_score(
            hand_cards,
            current_jokers=current_jokers,
            deck_name=deck_name,
            debuffed_ranks=debuffed_ranks,
            debuffed_suits=debuffed_suits,
            allows_four_card_hands=allows_four_card_hands,
            allows_gaps=allows_gaps,
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
        return {
            "baseline_score": baseline,
            "projected_score": best_score,
            "delta": delta,
            "delta_pct": round(delta_pct, 2),
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

        safe: List[Dict[str, Any]] = []
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
            loss_pct = ((baseline - trimmed_score) / max(1, baseline)) * 100.0
            if loss_pct <= tolerance_pct:
                joker = current_jokers[idx]
                safe.append(
                    {
                        "index": idx + 1,
                        "label": joker.get("label") or joker.get("key") or f"Joker {idx + 1}",
                        "loss_pct": round(loss_pct, 2),
                    }
                )

        safe.sort(key=lambda row: row.get("loss_pct", 0.0))
        return safe

    @staticmethod
    def find_best_discard(
        hand_cards: List[Dict[str, Any]], best_play_indices: List[int]
    ) -> List[int]:
        """
        Discards up to 5 cards to improve hand potential.
        Prioritizes completing draws (flushes, straights) over just discarding low cards.
        Returns 1-based indices as expected by the Lua API.
        """
        # Get indices and cards that are not part of the best play
        unused = [
            (idx, card)
            for idx, card in enumerate(hand_cards, start=1)
            if idx not in best_play_indices
        ]

        if not unused:
            return []

        # 1. Check for 4-card flush draw (consider full hand, not only unused)
        all_suits = [card["value"]["suit"] for card in hand_cards]
        suit_counts = Counter(all_suits)
        flush_suit = None
        for suit, count in suit_counts.items():
            if count >= 4:
                flush_suit = suit
                break

        if flush_suit:
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
            return options

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
            ]
            if any(nt in name for nt in no_target_names):
                priority = 60.0
                reasoning = f"Use {name.capitalize()} for immediate value."

                if "jupiter" in name and deck_upper == "CHECKERED":
                    priority = 95.0
                    reasoning = "Use Jupiter now to level Flush for Checkered consistency."
                elif any(
                    planet in name
                    for planet in [
                        "pluto",
                        "mercury",
                        "uranus",
                        "venus",
                        "saturn",
                        "earth",
                        "mars",
                        "neptune",
                        "planet x",
                        "ceres",
                        "eris",
                    ]
                ):
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

            elif "strength" in name and len(indexed_hand) >= 1:
                targets = [card[0] for card in indexed_hand[:2]]
                scored_options.append(
                    {
                        "priority": 66.0,
                        "action": "use_consumable",
                        "indices": [c_idx] + targets,
                        "reasoning": f"Use Strength to upgrade lowest cards: {targets}.",
                    }
                )

            elif "hanged man" in name and len(indexed_hand) >= 1:
                targets = [card[0] for card in indexed_hand[:2]]
                scored_options.append(
                    {
                        "priority": 64.0,
                        "action": "use_consumable",
                        "indices": [c_idx] + targets,
                        "reasoning": f"Use Hanged Man to destroy lowest value cards: {targets}.",
                    }
                )

        scored_options.sort(key=lambda row: float(row.get("priority", 0.0)), reverse=True)
        top = scored_options[:4]
        options: List[Dict[str, Any]] = []
        for idx, option in enumerate(top):
            options.append(
                {
                    "label": labels[idx] if idx < len(labels) else f"C{idx + 1}",
                    "action": option.get("action", "use_consumable"),
                    "indices": option.get("indices", []),
                    "reasoning": str(option.get("reasoning") or "Use consumable for value."),
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

            options.append(
                {
                    "label": labels[i - 1] if (i - 1) < len(labels) else f"P{i}",
                    "action": "choose_pack",
                    "indices": [i],
                    "reasoning": reasoning,
                }
            )

        return options
