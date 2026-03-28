import itertools
from collections import Counter
from typing import List, Dict, Any

# Map Balatro's string ranks to sortable integer values
RANK_VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, 'T': 10,
    'J': 11, 'Q': 12, 'K': 13, 'A': 14
}


class BalatroAlgorithm:
    @staticmethod
    def _evaluate_hand(cards: List[Dict[str, Any]]) -> tuple[int, str]:
        """
        Internal method. Evaluates a specific combination of up to 5 cards.
        Returns a tuple: (Arbitrary Score Weight, Poker Hand Name)
        """
        if not cards:
            return (0, "High Card")

        ranks = [RANK_VALUES.get(c['value']['rank'], 2) for c in cards]
        suits = [c['value']['suit'] for c in cards]

        ranks.sort(reverse=True)
        rank_counts = Counter(ranks)
        counts = list(rank_counts.values())
        counts.sort(reverse=True)

        is_flush = len(cards) == 5 and len(set(suits)) == 1

        # Straight detection logic
        is_straight = False
        if len(cards) == 5:
            if ranks[0] - ranks[4] == 4 and len(set(ranks)) == 5:
                is_straight = True
            elif ranks == [14, 5, 4, 3, 2]:
                is_straight = True

        if is_flush and is_straight:
            if ranks[0] == 14 and ranks[1] == 13:
                return (900, "Royal Flush")
            return (800, "Straight Flush")
        if counts == [4, 1] or counts == [4]:
            return (700, "Four of a Kind")
        if counts == [3, 2]:
            return (600, "Full House")
        if is_flush:
            return (500, "Flush")
        if is_straight:
            return (400, "Straight")
        if counts == [3, 1, 1] or counts == [3]:
            return (300, "Three of a Kind")
        if counts == [2, 2, 1] or counts == [2, 2]:
            return (200, "Two Pair")
        if counts == [2, 1, 1, 1] or counts == [2]:
            return (100, "Pair")

        return (ranks[0], "High Card")

    @staticmethod
    def find_best_hand(hand_cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generates all combinations and returns the best hand name and 1-based card indices.
        """
        if not hand_cards:
            return {"hand_name": "High Card", "card_indices": []}

        best_score = -1
        best_name = "High Card"
        best_combo = []

        max_combo_length = min(5, len(hand_cards))

        for r in range(1, max_combo_length + 1):
            for combo in itertools.combinations(hand_cards, r):
                score, name = BalatroAlgorithm._evaluate_hand(list(combo))

                if score > best_score or (score == best_score and len(combo) > len(best_combo)):
                    best_score = score
                    best_name = name
                    best_combo = combo

        return {
            "hand_name": best_name,
            "card_indices": [hand_cards.index(c) + 1 for c in best_combo],
        }

    @staticmethod
    def find_best_discard(hand_cards: List[Dict[str, Any]], best_play_indices: List[int]) -> List[int]:
        """
        Discards up to 5 of the lowest value cards that are not part of the best play.
        Returns 1-based indices as expected by the Lua API.
        """
        unused_cards = [c for i, c in enumerate(hand_cards) if (i + 1) not in best_play_indices]

        unused_cards.sort(key=lambda c: RANK_VALUES.get(c['value']['rank'], 2))

        discard_combo = unused_cards[:5]

        return [hand_cards.index(c) + 1 for c in discard_combo]
