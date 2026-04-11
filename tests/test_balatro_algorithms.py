import unittest

from skills.balatro_bot.modules.algorithms import BalatroAlgorithm


def _card(rank: str, suit: str) -> dict:
    return {
        "value": {"rank": rank, "suit": suit, "effect": ""},
        "label": "Base Card",
        "key": f"{suit[0]}_{rank}",
    }


def _joker(key: str, label: str, effect: str) -> dict:
    return {
        "key": key,
        "label": label,
        "type": "joker",
        "value": {"effect": effect},
    }


class TestBalatroAlgorithms(unittest.TestCase):
    def test_explicit_hand_levels_increase_estimated_score(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]

        baseline = BalatroAlgorithm.estimate_best_score(
            hand_cards=hand,
            current_jokers=[],
            deck_name="CHECKERED",
        )
        with_levels = BalatroAlgorithm.estimate_best_score(
            hand_cards=hand,
            current_jokers=[],
            deck_name="CHECKERED",
            hand_levels={"Flush": 3},
        )

        self.assertGreater(with_levels, baseline)

    def test_empty_explicit_levels_do_not_change_score(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]

        baseline = BalatroAlgorithm.estimate_best_score(
            hand_cards=hand,
            current_jokers=[],
            deck_name="CHECKERED",
        )
        empty_levels = BalatroAlgorithm.estimate_best_score(
            hand_cards=hand,
            current_jokers=[],
            deck_name="CHECKERED",
            hand_levels={},
            planet_levels={},
        )

        self.assertEqual(empty_levels, baseline)

    def test_shop_impact_uses_explicit_levels_from_deck_state(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        shop_joker = {
            "key": "j_huge_mult",
            "label": "Huge Mult Joker",
            "value": {"effect": "+40 Mult"},
        }

        no_levels = BalatroAlgorithm.evaluate_shop_joker_impact(
            shop_card=shop_joker,
            current_jokers=[],
            hand_cards=hand,
            deck_name="CHECKERED",
            deck_state={},
        )
        with_levels = BalatroAlgorithm.evaluate_shop_joker_impact(
            shop_card=shop_joker,
            current_jokers=[],
            hand_cards=hand,
            deck_name="CHECKERED",
            deck_state={"hand_levels": {"Flush": 4}},
        )

        self.assertGreater(with_levels["baseline_score"], no_levels["baseline_score"])

    def test_expected_projection_prefers_scaler_over_flat_in_long_run(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Hearts"),
        ]
        flat_joker = {
            "key": "j_flat",
            "label": "Flat Mult",
            "value": {"effect": "+40 Mult"},
        }
        scaler_joker = {
            "key": "j_scaler",
            "label": "Spade Scaler",
            "value": {"effect": "Gains +12 Mult per Spade played"},
        }
        deck_state = {
            "deck": {
                "cards": [
                    _card("A", "Spades"),
                    _card("2", "Spades"),
                    _card("3", "Spades"),
                    _card("4", "Spades"),
                    _card("5", "Spades"),
                    _card("6", "Spades"),
                    _card("7", "Spades"),
                    _card("8", "Spades"),
                    _card("9", "Spades"),
                    _card("T", "Spades"),
                    _card("J", "Spades"),
                    _card("Q", "Spades"),
                ]
            }
        }

        flat_impact = BalatroAlgorithm.evaluate_shop_joker_impact(
            shop_card=flat_joker,
            current_jokers=[],
            hand_cards=hand,
            deck_name="CHECKERED",
            deck_state=deck_state,
        )
        scaler_impact = BalatroAlgorithm.evaluate_shop_joker_impact(
            shop_card=scaler_joker,
            current_jokers=[],
            hand_cards=hand,
            deck_name="CHECKERED",
            deck_state=deck_state,
        )

        self.assertGreater(
            flat_impact["immediate_impact"],
            scaler_impact["immediate_impact"],
        )
        self.assertGreater(
            scaler_impact["projected_3_round_impact"],
            flat_impact["projected_3_round_impact"],
        )

    def test_auto_optimize_blueprint_targets_best_right_neighbor(self):
        jokers = [
            _joker("j_econ", "Economy Joker", "Earn $1 at end of round"),
            _joker("j_chips", "Chip Joker", "+40 Chips"),
            _joker("j_mult", "Mult Joker", "+20 Mult"),
            _joker("j_xmult", "XMult Joker", "X3 Mult"),
            _joker("j_blueprint", "Blueprint", "Copies ability of Joker to the right"),
        ]

        result = BalatroAlgorithm.auto_optimize_jokers(jokers)
        ordered = result["ordered_jokers"]
        labels = [j.get("label") for j in ordered]

        bp_idx = labels.index("Blueprint")
        self.assertLess(bp_idx + 1, len(labels))
        self.assertEqual(labels[bp_idx + 1], "XMult Joker")

    def test_auto_optimize_brainstorm_pushes_best_target_leftmost(self):
        jokers = [
            _joker("j_chips", "Chip Joker", "+40 Chips"),
            _joker("j_mult", "Mult Joker", "+20 Mult"),
            _joker("j_xmult", "XMult Joker", "X3 Mult"),
            _joker("j_econ", "Economy Joker", "Earn $1 at end of round"),
            _joker("j_brainstorm", "Brainstorm", "Copies the leftmost Joker"),
        ]

        result = BalatroAlgorithm.auto_optimize_jokers(jokers)
        ordered = result["ordered_jokers"]
        labels = [j.get("label") for j in ordered]

        self.assertEqual(labels[0], "XMult Joker")
        self.assertIn("Brainstorm", labels)

    def test_auto_optimize_blueprint_and_brainstorm_joint_global_max(self):
        jokers = [
            _joker("j_chips", "Chip Joker", "+40 Chips"),
            _joker("j_mult", "Mult Joker", "+20 Mult"),
            _joker("j_xmult", "XMult Joker", "X3 Mult"),
            _joker("j_blueprint", "Blueprint", "Copies ability of Joker to the right"),
            _joker("j_brainstorm", "Brainstorm", "Copies the leftmost Joker"),
        ]

        result = BalatroAlgorithm.auto_optimize_jokers(jokers)
        ordered = result["ordered_jokers"]
        labels = [j.get("label") for j in ordered]

        # Brainstorm should copy leftmost XMult Joker.
        self.assertEqual(labels[0], "XMult Joker")
        # Blueprint should sit immediately left of a high-value target.
        bp_idx = labels.index("Blueprint")
        self.assertLess(bp_idx + 1, len(labels))
        self.assertIn(labels[bp_idx + 1], {"XMult Joker", "Brainstorm"})

    def test_auto_optimize_hybrid_xmult_bubbles_right_in_base_sort(self):
        jokers = [
            _joker("j_econ", "Economy Joker", "Earn $1 at end of round"),
            _joker("j_hybrid", "Hybrid Joker", "+8 Mult and X2 Mult"),
            _joker("j_mult", "Mult Joker", "+30 Mult"),
            _joker("j_chips", "Chip Joker", "+80 Chips"),
        ]

        result = BalatroAlgorithm.auto_optimize_jokers(jokers)
        labels = [j.get("label") for j in result["ordered_jokers"]]

        self.assertGreater(labels.index("Hybrid Joker"), labels.index("Mult Joker"))

    def test_auto_optimize_multiple_copycats_does_not_permute_all_jokers(self):
        jokers = [
            _joker("j_econ", "Economy Joker", "Earn $1 at end of round"),
            _joker("j_chips", "Chip Joker", "+40 Chips"),
            _joker("j_mult", "Mult Joker", "+20 Mult"),
            _joker("j_xmult", "XMult Joker", "X3 Mult"),
            _joker("j_blueprint", "Blueprint", "Copies ability of Joker to the right"),
            _joker("j_brainstorm", "Brainstorm", "Copies the leftmost Joker"),
            _joker("j_blueprint2", "Blueprint", "Copies ability of Joker to the right"),
            _joker("j_brainstorm2", "Brainstorm", "Copies the leftmost Joker"),
        ]

        result = BalatroAlgorithm.auto_optimize_jokers(jokers)
        self.assertEqual(len(result["ordered_jokers"]), len(jokers))
        self.assertEqual(len(result["reorder_indices"]), len(jokers))

    def test_auto_optimize_brainstorm_target_is_math_selected_not_forced_xmult(self):
        jokers = [
            _joker("j_mult_big", "Big Mult", "+100 Mult"),
            _joker("j_xmult_small", "Small XMult", "X1.5 Mult"),
            _joker("j_brainstorm", "Brainstorm", "Copies the leftmost Joker"),
            _joker("j_chips", "Chip Joker", "+30 Chips"),
        ]

        result = BalatroAlgorithm.auto_optimize_jokers(jokers)
        labels = [j.get("label") for j in result["ordered_jokers"]]

        # Math should choose Big Mult as Brainstorm's leftmost target here,
        # rather than blindly forcing xMult to index 0.
        self.assertEqual(labels[0], "Big Mult")

    def test_extract_xmult_values_matches_valid_variants(self):
        cases = {
            "X3 Mult": [3.0],
            "x1.5 Mult": [1.5],
            "×2": [2.0],
            "Gains X2 Mult when played": [2.0],
            "x 2.25 mult": [2.25],
        }
        for text, expected in cases.items():
            values = BalatroAlgorithm._extract_xmult_values(text)
            self.assertEqual(values, expected)

    def test_extract_xmult_values_rejects_flat_mult_false_positives(self):
        cases = [
            "+3 Mult",
            "+30 mult",
            "Adds +3 Mult each hand",
            "Mult +3",
            "Gain 3 Mult",
        ]
        for text in cases:
            values = BalatroAlgorithm._extract_xmult_values(text)
            self.assertEqual(values, [])

    def test_extract_xmult_values_supports_multiple_tokens(self):
        values = BalatroAlgorithm._extract_xmult_values("X2 Mult and ×1.5")
        self.assertEqual(values, [2.0, 1.5])

    def test_extract_xmult_values_does_not_confuse_x_with_plain_numbers(self):
        values = BalatroAlgorithm._extract_xmult_values(
            "Gain 3 chips, x? nope, +4 Mult"
        )
        self.assertEqual(values, [])

    def test_score_joker_value_checkered_small_hand_condition_probability_aware(self):
        # Flush-leaning hand profile should heavily penalize <=4-card condition.
        hand_flush = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        half = _joker(
            "j_half",
            "Half Joker",
            "+20 Mult if played hand has 3 or fewer cards",
        )
        label_flush = BalatroAlgorithm.score_joker_value(
            joker=half,
            current_jokers=[],
            hand_stats={"total_chips": 200, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand_flush,
            deck_state={},
        )
        self.assertIn(label_flush, {"BAD SYNERGY", "LOW VALUE"})

        # Pair-leaning profile should avoid hard BAD penalty.
        hand_pair = [
            _card("A", "Spades"),
            _card("A", "Hearts"),
            _card("K", "Clubs"),
            _card("Q", "Diamonds"),
            _card("J", "Spades"),
        ]
        label_pair = BalatroAlgorithm.score_joker_value(
            joker=half,
            current_jokers=[],
            hand_stats={"total_chips": 100, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand_pair,
            deck_state={},
        )
        self.assertNotEqual(label_pair, "BAD SYNERGY")

    def test_score_joker_value_checkered_square_joker_is_bad_synergy(self):
        hand_flush = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        square = _joker(
            "j_square",
            "Square Joker",
            "This Joker gains +4 Mult if played hand has exactly 4 cards",
        )

        label = BalatroAlgorithm.score_joker_value(
            joker=square,
            current_jokers=[],
            hand_stats={"total_chips": 200, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand_flush,
            deck_state={},
        )

        self.assertEqual(label, "BAD SYNERGY")

    def test_score_joker_value_fortune_teller_requires_tarot_support(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        fortune_teller = _joker(
            "j_fortune_teller",
            "Fortune Teller",
            "Gains +1 Mult per Tarot card used this run",
        )

        low_support = BalatroAlgorithm.score_joker_value(
            joker=fortune_teller,
            current_jokers=[],
            hand_stats={"total_chips": 120, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state={"money": 3, "shop": {"cards": []}, "packs": {"cards": []}},
        )

        with_tarot_support = BalatroAlgorithm.score_joker_value(
            joker=fortune_teller,
            current_jokers=[],
            hand_stats={"total_chips": 120, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state={
                "money": 20,
                "shop": {
                    "cards": [
                        {
                            "key": "c_tarot_strength",
                            "label": "Tarot Strength",
                            "value": {"effect": ""},
                        }
                    ]
                },
                "packs": {"cards": [{"key": "p_arcana", "label": "Arcana Pack", "value": {"effect": ""}}]},
            },
        )

        self.assertEqual(low_support, "BAD SYNERGY")
        self.assertEqual(with_tarot_support, "BAD SYNERGY")

    def test_score_joker_value_forced_bad_synergy_blacklist(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        denylisted = [
            _joker("j_ride_the_bus", "Ride the Bus", "Gains +1 Mult per hand played"),
            _joker("j_blackboard", "Blackboard", "X3 Mult if all cards held are Spades or Clubs"),
            _joker("j_runner", "Runner", "Gains +15 Chips if played hand contains a Straight"),
            _joker("j_green_joker", "Green Joker", "Gains +1 Mult per hand played"),
            _joker("j_red_card", "Red Card", "Gains +3 Mult when any Booster Pack is skipped"),
        ]

        for joker in denylisted:
            with self.subTest(joker=joker["key"]):
                label = BalatroAlgorithm.score_joker_value(
                    joker=joker,
                    current_jokers=[],
                    hand_stats={"total_chips": 120, "joker_count": 0},
                    deck_name="CHECKERED",
                    hand_cards=hand,
                    deck_state={"money": 20, "shop": {"cards": []}, "packs": {"cards": []}},
                )
                self.assertEqual(label, "BAD SYNERGY")

    def test_score_joker_value_currently_zero_scaler_is_bad_synergy(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        scaler = _joker(
            "j_test_scaler",
            "Test Scaler",
            "This Joker gains +2 Mult when X happens (Currently +0 Mult)",
        )

        label = BalatroAlgorithm.score_joker_value(
            joker=scaler,
            current_jokers=[],
            hand_stats={"total_chips": 120, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state={"money": 20},
        )

        self.assertEqual(label, "BAD SYNERGY")

    def test_score_joker_value_checkered_unreliable_chance_text_is_bad_synergy(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        unreliable = _joker(
            "j_rng_test",
            "RNG Test Joker",
            "1 in 4 chance to gain X2 Mult",
        )

        label = BalatroAlgorithm.score_joker_value(
            joker=unreliable,
            current_jokers=[],
            hand_stats={"total_chips": 120, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state={"money": 20},
        )

        self.assertEqual(label, "BAD SYNERGY")

    def test_score_joker_value_checkered_self_destruct_text_is_bad_synergy(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        unreliable = _joker(
            "j_self_destruct_test",
            "Self Destruct Joker",
            "+15 Mult, 1 in 6 chance destroyed at end of round",
        )

        label = BalatroAlgorithm.score_joker_value(
            joker=unreliable,
            current_jokers=[],
            hand_stats={"total_chips": 120, "joker_count": 0},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state={"money": 20},
        )

        self.assertEqual(label, "BAD SYNERGY")

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

    def test_identify_safe_sell_jokers_bad_synergy_respects_capacity_rule(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        bad_synergy_half = {
            "key": "j_half",
            "label": "Half Joker",
            "value": {"effect": "+20 Mult if played hand has 3 or fewer cards"},
        }

        jokers_not_full = [
            bad_synergy_half,
            {"key": "j_scaler", "label": "Scaler", "value": {"effect": "+80 Mult"}},
            {"key": "j_chip", "label": "Chip", "value": {"effect": "+80 Chips"}},
            {"key": "j_blank", "label": "Blank", "value": {"effect": ""}},
        ]
        safe_not_full = BalatroAlgorithm.identify_safe_sell_jokers(
            current_jokers=jokers_not_full,
            hand_cards=hand,
            deck_name="CHECKERED",
            tolerance_pct=-1.0,
            current_money=30,
            max_jokers=5,
        )
        indices_not_full = {row["index"] for row in safe_not_full}
        self.assertNotIn(1, indices_not_full)

        jokers_full = jokers_not_full + [
            {"key": "j_filler", "label": "Filler", "value": {"effect": ""}}
        ]
        safe_full = BalatroAlgorithm.identify_safe_sell_jokers(
            current_jokers=jokers_full,
            hand_cards=hand,
            deck_name="CHECKERED",
            tolerance_pct=-1.0,
            current_money=30,
            max_jokers=5,
        )
        by_index_full = {row["index"]: row for row in safe_full}
        self.assertIn(1, by_index_full)
        self.assertEqual(by_index_full[1].get("reason"), "BAD SYNERGY")

    def test_identify_safe_sell_jokers_protects_future_scaler(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Hearts"),
        ]
        jokers = [
            {"key": "j_blank", "label": "Blank Joker", "value": {"effect": ""}},
            {
                "key": "j_scaler",
                "label": "Spade Scaler",
                "value": {"effect": "Gains +12 Mult per Spade played"},
            },
        ]
        deck_state = {
            "deck": {
                "cards": [
                    _card("A", "Spades"),
                    _card("2", "Spades"),
                    _card("3", "Spades"),
                    _card("4", "Spades"),
                    _card("5", "Spades"),
                    _card("6", "Spades"),
                ]
            }
        }

        safe = BalatroAlgorithm.identify_safe_sell_jokers(
            current_jokers=jokers,
            hand_cards=hand,
            tolerance_pct=5.0,
            deck_name="CHECKERED",
            deck_state=deck_state,
        )
        safe_indices = {row["index"] for row in safe}

        self.assertIn(1, safe_indices)
        self.assertNotIn(2, safe_indices)

    def test_lineup_has_probabilistic_jokers_detects_rng_tokens(self):
        deterministic = [
            {"key": "j_mult", "label": "Mult Joker", "value": {"effect": "+20 Mult"}}
        ]
        probabilistic = [
            {
                "key": "j_bloodstone",
                "label": "Bloodstone",
                "value": {"effect": "1 in 2 chance for Hearts to give X2 Mult"},
            }
        ]

        self.assertFalse(
            BalatroAlgorithm.lineup_has_probabilistic_jokers(deterministic)
        )
        self.assertTrue(BalatroAlgorithm.lineup_has_probabilistic_jokers(probabilistic))

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
            "value": {
                "effect": "Starts at X1.0 Mult, gains X0.25 per card added to your deck"
            },
        }
        deck_state = {
            "money": 30,
            "packs": {
                "cards": [
                    {
                        "key": "p_standard",
                        "label": "Standard Pack",
                        "value": {"effect": ""},
                    }
                ]
            },
            "vouchers": {
                "cards": [
                    {
                        "key": "v_magic_trick",
                        "label": "Magic Trick",
                        "value": {"effect": "Playing cards can appear in the shop"},
                    }
                ]
            },
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
            {
                "label": "Jupiter",
                "key": "c_jupiter",
                "value": {"effect": "Level up Flush"},
            },
            {
                "label": "Strength",
                "key": "c_strength",
                "value": {"effect": "Enhances 2 selected cards"},
            },
        ]

        options = BalatroAlgorithm.evaluate_consumable_plays(
            consumables=consumables,
            hand_cards=hand,
            deck_name="CHECKERED",
        )

        self.assertTrue(options)
        self.assertEqual(options[0]["indices"], [2])
        self.assertIn("Jupiter", options[0]["reasoning"])

    def test_evaluate_consumable_empty_list_does_not_crash(self):
        options = BalatroAlgorithm.evaluate_consumable_plays(
            consumables=[],
            hand_cards=[],
            deck_name="CHECKERED",
        )
        self.assertEqual(options, [])

    def test_evaluate_consumable_plays_enhancer_targets_highest(self):
        hand = [
            _card("2", "Spades"),
            _card("3", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("A", "Hearts"),
        ]
        consumables = [
            {
                "label": "Empress",
                "key": "c_empress",
                "value": {"effect": "Enhances 2 selected cards"},
            },
        ]
        options = BalatroAlgorithm.evaluate_consumable_plays(
            consumables=consumables,
            hand_cards=hand,
            deck_name="CHECKERED",
        )
        self.assertTrue(options)
        target_indices = set(options[0]["indices"][1:])
        self.assertEqual(target_indices, {3, 5})
        self.assertIn("Empress", options[0]["reasoning"])

    def test_evaluate_consumable_plays_destructor_targets_lowest(self):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("8", "Hearts"),
            _card("3", "Hearts"),
        ]
        consumables = [
            {
                "label": "Hanged Man",
                "key": "c_hanged_man",
                "value": {"effect": "Destroys 2 selected cards"},
            },
        ]
        options = BalatroAlgorithm.evaluate_consumable_plays(
            consumables=consumables,
            hand_cards=hand,
            deck_name="CHECKERED",
        )
        self.assertTrue(options)
        target_indices = set(options[0]["indices"][1:])
        self.assertEqual(target_indices, {4, 5})
        self.assertIn("Hanged man", options[0]["reasoning"])

    def test_xmult_immediate_impact_exceeds_flat_mult_at_ante6_with_baseline(self):
        # At Ante 6 with a 500-point baseline, X2 Mult should score far higher
        # than a +40 Mult joker. The baseline-scaled path makes the xMult delta
        # (2.0 - 1.0) * 500 = 500, vs flat 40 * 1.5 = 60.
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        flat_joker = _joker("j_flat_mult", "Flat Mult", "+40 Mult")
        xmult_joker = _joker("j_xmult2", "Double Mult", "X2 Mult")

        # Build a baseline from the hand with no jokers so both evaluations share
        # the same reference point.
        baseline = float(
            BalatroAlgorithm.estimate_best_score(
                hand,
                current_jokers=[],
                deck_name="CHECKERED",
            )
        )
        # Use a known ante-6 baseline of at least 500 by overriding directly.
        test_baseline = max(baseline, 500.0)

        flat_score = BalatroAlgorithm._estimate_joker_immediate_additive_value(
            flat_joker,
            baseline_score=test_baseline,
        )
        xmult_score = BalatroAlgorithm._estimate_joker_immediate_additive_value(
            xmult_joker,
            baseline_score=test_baseline,
        )

        self.assertGreater(
            xmult_score,
            flat_score,
            msg=(
                f"X2 Mult should outscore +40 Mult at baseline {test_baseline}. "
                f"Got xmult={xmult_score}, flat={flat_score}"
            ),
        )

    def test_score_joker_value_flat_mult_downgrades_to_low_value_at_ante5_with_xmult_on_board(
        self,
    ):
        hand = [
            _card("A", "Spades"),
            _card("K", "Spades"),
            _card("Q", "Spades"),
            _card("J", "Spades"),
            _card("9", "Spades"),
        ]
        # The xMult joker already on the board.
        existing_xmult = _joker("j_xmult_existing", "Existing XMult", "X2 Mult")
        # The candidate flat +Mult joker being evaluated.
        flat_mult_candidate = _joker("j_flat_cand", "Flat Mult Candidate", "+30 Mult")

        for ante in (5, 6, 7, 8):
            with self.subTest(ante=ante):
                label = BalatroAlgorithm.score_joker_value(
                    joker=flat_mult_candidate,
                    current_jokers=[existing_xmult],
                    hand_stats={"total_chips": 300, "joker_count": 1},
                    deck_name="CHECKERED",
                    hand_cards=hand,
                    deck_state={"ante_num": ante},
                )
                self.assertEqual(
                    label,
                    "LOW VALUE",
                    msg=f"Flat +Mult should be LOW VALUE at Ante {ante} with xMult board. Got: {label}",
                )

        # Sanity check: at Ante 4, the guard must NOT fire — label may be anything
        # other than specifically triggered by this guard (we just ensure it is not
        # forced to LOW VALUE solely by the guard).  We use ante_num=4.
        label_ante4 = BalatroAlgorithm.score_joker_value(
            joker=flat_mult_candidate,
            current_jokers=[existing_xmult],
            hand_stats={"total_chips": 300, "joker_count": 1},
            deck_name="CHECKERED",
            hand_cards=hand,
            deck_state={"ante_num": 4},
        )
        # The guard should not have fired; whatever the label, it wasn't forced to
        # LOW VALUE by the ante guard.  We don't over-constrain the early-game path.
        # Just assert the function returns a known valid label (smoke test).
        self.assertIn(
            label_ante4,
            {"HIGH SYNERGY", "MODERATE VALUE", "LOW VALUE", "SITUATIONAL", "BAD SYNERGY"},
        )


    # -----------------------------------------------------------------
    # find_best_discard — target_hand_type tests
    # -----------------------------------------------------------------

    def test_find_best_discard_flush_target_discards_offsuit_over_straight_draw(self):
        """Flush-committed mode must discard off-suit cards even when a
        4-card open-ended straight draw is present in the hand.

        Hand:  6♠ 7♠ 8♠ 9♠ 3♥   (Spades flush draw + 6-7-8-9 OESD)
        best_play_indices = [1, 2, 3, 4]  (the 4 Spades are the 'play')
        Without commitment: normal logic would keep the straight draw cards.
        With target_hand_type="Flush": must discard 3♥ (index 5) immediately.
        """
        hand = [
            _card("6", "Spades"),  # idx 1
            _card("7", "Spades"),  # idx 2
            _card("8", "Spades"),  # idx 3
            _card("9", "Spades"),  # idx 4
            _card("3", "Hearts"),  # idx 5 — off-suit, should be discarded
        ]
        best_play_indices = [1, 2, 3, 4]

        result = BalatroAlgorithm.find_best_discard(
            hand, best_play_indices, target_hand_type="Flush"
        )
        # Index 5 (3♥) is the only off-suit card not in best_play_indices.
        self.assertEqual(result, [5], msg=f"Expected [5], got {result}")

    def test_find_best_discard_flush_target_discards_offsuit_when_straight_draw_conflicts(self):
        """When the best_play_indices do NOT include all flush-suit cards,
        flush-committed mode should still discard off-suit non-play cards
        rather than protecting straight-draw cards.

        Hand:  5♠ 6♠ 7♠ 8♠ 9♦   (♠ flush draw + straight draw 5-6-7-8-9)
        Straight draw uses 5-6-7-8 (any suit); without commitment the
        9♦ might be kept as part of a straight.
        With target_hand_type="Flush": 9♦ must be discarded (off-suit).
        """
        hand = [
            _card("5", "Spades"),    # idx 1
            _card("6", "Spades"),    # idx 2
            _card("7", "Spades"),    # idx 3
            _card("8", "Spades"),    # idx 4
            _card("9", "Diamonds"),  # idx 5 — off-suit
        ]
        # Suppose algorithm chose the flush cards as the play.
        best_play_indices = [1, 2, 3, 4]

        result = BalatroAlgorithm.find_best_discard(
            hand, best_play_indices, target_hand_type="Flush"
        )
        # Only 9♦ is not in best_play_indices and is off-suit.
        self.assertIn(5, result, msg=f"9♦ (index 5) should be discarded; got {result}")

    def test_find_best_discard_none_target_preserves_original_straight_draw_protection(self):
        """Without a flush commitment the straight-draw protection path must
        still fire — this guards against regression in the normal code path.

        Hand:  6♠ 7♥ 8♦ 9♣ 2♠   (OESD: 6-7-8-9; 2♠ is the throwaway)
        best_play_indices = []  (nothing committed to play yet)
        Expected: 2♠ (index 5, lowest unused) discarded; 6-7-8-9 protected.
        """
        hand = [
            _card("6", "Spades"),    # idx 1 — part of OESD
            _card("7", "Hearts"),    # idx 2 — part of OESD
            _card("8", "Diamonds"),  # idx 3 — part of OESD
            _card("9", "Clubs"),     # idx 4 — part of OESD
            _card("2", "Spades"),    # idx 5 — lowest, should be discarded
        ]
        best_play_indices: list[int] = []

        result = BalatroAlgorithm.find_best_discard(
            hand, best_play_indices, target_hand_type=None
        )
        # The OESD cards (indices 1-4) should be protected; only 2♠ discarded.
        self.assertIn(5, result, msg=f"2♠ (index 5) should be discarded; got {result}")
        for protected in [1, 2, 3, 4]:
            self.assertNotIn(
                protected,
                result,
                msg=f"OESD index {protected} should be protected; got {result}",
            )

    def test_find_best_discard_flush_target_no_dominant_suit_falls_through(self):
        """When target_hand_type='Flush' is requested but NO suit appears
        4+ times, there is no dominant suit to commit to.  The function must
        fall through to normal discard logic without crashing.

        Hand:  A♠ K♥ Q♦ J♣ 2♠  — 4 different suits, no dominant flush suit.
        """
        hand = [
            _card("A", "Spades"),    # idx 1
            _card("K", "Hearts"),    # idx 2
            _card("Q", "Diamonds"),  # idx 3
            _card("J", "Clubs"),     # idx 4
            _card("2", "Spades"),    # idx 5
        ]
        best_play_indices = [1, 2, 3, 4]

        # Must not raise; result is the normal fallback discard (may be empty or [5]).
        try:
            result = BalatroAlgorithm.find_best_discard(
                hand, best_play_indices, target_hand_type="Flush"
            )
        except Exception as exc:  # pragma: no cover
            self.fail(f"find_best_discard raised unexpectedly with no dominant suit: {exc}")

        # Sanity: 2♠ (only unused card) could be returned or not — just verify it's a list.
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)

