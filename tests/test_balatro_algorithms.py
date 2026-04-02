import unittest

from skills.balatro_bot.modules.algorithms import BalatroAlgorithm


def _card(rank: str, suit: str) -> dict:
    return {
        "value": {"rank": rank, "suit": suit, "effect": ""},
        "label": "Base Card",
        "key": f"{suit[0]}_{rank}",
    }


class TestBalatroAlgorithms(unittest.TestCase):
    def test_evaluate_shop_joker_impact_non_joker_is_zero(self):
        hand = [_card("A", "Spades"), _card("K", "Spades"), _card("Q", "Spades")]
        shop_card = {"key": "c_jupiter", "label": "Jupiter", "value": {"effect": ""}}

        impact = BalatroAlgorithm.evaluate_shop_joker_impact(
            shop_card=shop_card,
            current_jokers=[],
            hand_cards=hand,
            deck_name="CHECKERED",
        )

        self.assertEqual(impact["delta"], 0)
        self.assertEqual(impact["delta_pct"], 0.0)
        self.assertIsNone(impact["replacement_index"])

    def test_evaluate_shop_joker_impact_positive_delta(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("T", "Spades"),
        ]
        shop_joker = {
            "key": "j_huge_mult",
            "label": "Huge Mult Joker",
            "value": {"effect": "+40 Mult"},
        }

        impact = BalatroAlgorithm.evaluate_shop_joker_impact(
            shop_card=shop_joker,
            current_jokers=[],
            hand_cards=hand,
            deck_name="CHECKERED",
        )

        self.assertGreater(impact["delta"], 0)
        self.assertGreater(impact["delta_pct"], 0.0)

    def test_identify_safe_sell_jokers_prefers_low_impact(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        jokers = [
            {"key": "j_blank", "label": "Blank Joker", "value": {"effect": ""}},
            {
                "key": "j_scaler",
                "label": "Scaler Joker",
                "value": {"effect": "+80 Mult"},
            },
        ]

        safe = BalatroAlgorithm.identify_safe_sell_jokers(
            current_jokers=jokers,
            hand_cards=hand,
            tolerance_pct=3.0,
            deck_name="CHECKERED",
        )

        safe_indices = {row["index"] for row in safe}
        self.assertIn(1, safe_indices)
        self.assertNotIn(2, safe_indices)

    def test_score_voucher_value_overstock_is_high(self):
        voucher = {
            "key": "v_overstock",
            "label": "Overstock",
            "value": {"effect": "Shop has one extra card slot"},
        }
        self.assertEqual(
            BalatroAlgorithm.score_voucher_value(voucher, money=18, ante=2),
            "HIGH SYNERGY",
        )

    def test_score_joker_value_uses_impact_with_hand_cards(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        joker = {
            "key": "j_big_mult",
            "label": "Big Mult Joker",
            "value": {"effect": "+60 Mult"},
        }

        label = BalatroAlgorithm.score_joker_value(
            joker=joker,
            current_jokers=[],
            hand_stats={"total_chips": 10, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
        )

        self.assertIn(label, {"HIGH SYNERGY", "MODERATE VALUE"})

    def test_score_joker_value_hologram_uses_scaling_state(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Hearts"),
            _card("Q", "Spades"),
            _card("J", "Hearts"),
            _card("9", "Spades"),
        ]
        joker = {
            "key": "j_hologram",
            "label": "Hologram",
            "value": {"effect": "Starts at X1.0 Mult, gains X0.25 per card added to your deck"},
        }
        deck_state = {
            "money": 30,
            "packs": {"cards": [{"key": "p_standard", "label": "Standard Pack", "value": {"effect": ""}}]},
            "vouchers": {"cards": [{"key": "v_magic_trick", "label": "Magic Trick", "value": {"effect": "Playing cards can appear in the shop"}}]},
            "hand": {"cards": hand},
        }

        label = BalatroAlgorithm.score_joker_value(
            joker=joker,
            current_jokers=[],
            hand_stats={"total_chips": 150, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state=deck_state,
        )

        self.assertEqual(label, "HIGH SYNERGY")

    def test_evaluate_consumable_plays_prioritizes_jupiter_for_checkered(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("8", "Hearts"),
            _card("3", "Hearts"),
        ]
        consumables = [
            {"label": "Hermit", "key": "c_hermit", "value": {"effect": ""}},
            {"label": "Jupiter", "key": "c_jupiter", "value": {"effect": "Level up Flush"}},
            {"label": "Strength", "key": "c_strength", "value": {"effect": "Enhances 2 selected cards"}},
        ]

        options = BalatroAlgorithm.evaluate_consumable_plays(
            consumables=consumables,
            hand_cards=hand,
            deck_name="CHECKERED",
        )

        self.assertTrue(options)
        self.assertEqual(options[0]["indices"], [2])
        self.assertIn("Jupiter", options[0]["reasoning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
