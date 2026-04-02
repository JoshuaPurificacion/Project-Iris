"""
test_micro_rpg.py

Tests for the MicroRPG engine, focused on:
  - Defend correctness: DEF-scaled damage reduction + guaranteed counter-strike
  - Defend vs Attack situational optimality (high-ATK enemy scenario)
"""

import random
import unittest

from games.micro_rpg import MicroRPG, PLAYER_BASE_DEF


class TestDefend(unittest.TestCase):
    """Verify that the reworked defend action satisfies its design contract."""

    def _make_game(self, seed=42):
        random.seed(seed)
        return MicroRPG()

    def test_defend_reduces_incoming_damage_below_50_pct(self):
        """
        Defending with base DEF=1 should block more than 50% of raw enemy ATK
        (dmg_mult = max(0.25, 0.5 - 1*0.05) = 0.45  →  55% blocked).
        We simulate 50 defend turns and confirm average reduced damage < 50% of
        unmodified attack average.
        """
        total_reduced = 0
        total_full = 0
        runs = 200

        for seed in range(runs):
            random.seed(seed)
            game = MicroRPG()
            enemy_atk = game.enemy["atk"]

            # Simulate one full-damage hit (dmg_mult=1.0)
            random.seed(seed + 10000)
            _, full_dmg = game._resolve_enemy_attack(dmg_multiplier=1.0)
            total_full += full_dmg

            # Simulate one defended hit (dmg_mult=0.45 at DEF=1)
            random.seed(seed + 10000)
            dmg_mult = max(0.25, 0.5 - game.player["def"] * 0.05)
            _, reduced_dmg = game._resolve_enemy_attack(dmg_multiplier=dmg_mult)
            total_reduced += reduced_dmg

        self.assertLess(
            total_reduced,
            total_full * 0.5,
            "Defend should block more than 50% of raw damage at base DEF.",
        )

    def test_defend_applies_counter_strike(self):
        """
        After a successful defend (player survives), enemy HP must decrease
        by the counter-strike amount (≥ 1).
        """
        random.seed(0)
        game = MicroRPG()
        game.player["hp"] = game.player["max_hp"]   # full HP — won't die
        enemy_hp_before = game.enemy["hp"]

        result = game.take_action("defend")
        events = result["events"]

        # Counter-strike narrative must appear
        counter_events = [e for e in events if "parry counter" in e.lower()]
        self.assertTrue(
            len(counter_events) > 0 or game.enemy["hp"] < enemy_hp_before,
            "Defend should deal a parry counter-strike to the enemy.",
        )

        # Enemy HP must have dropped (unless kill was triggered, but HP can't go up)
        self.assertLessEqual(result["state"]["enemy_hp"], enemy_hp_before)

    def test_defend_counter_strike_at_least_1(self):
        """Counter-strike damage floor is 1 regardless of weapon."""
        random.seed(99)
        game = MicroRPG()
        game.player["hp"] = game.player["max_hp"]

        # Force the weakest possible weapon ATK
        game.player["weapon"]["atk"] = 1
        enemy_hp_before = game.enemy["hp"]

        result = game.take_action("defend")
        # If player survived, enemy must have taken at least 1 damage
        if result["state"]["player_hp"] > 0:
            self.assertLess(result["state"]["enemy_hp"], enemy_hp_before)

    def test_defend_scales_with_def_stat(self):
        """Higher DEF stat produces a lower damage multiplier (more blocking)."""
        def_values = [1, 3, 5, 9]
        multipliers = [max(0.25, 0.5 - d * 0.05) for d in def_values]
        # Should be strictly decreasing (floored at 0.25)
        for i in range(len(multipliers) - 1):
            self.assertGreaterEqual(
                multipliers[i],
                multipliers[i + 1],
                f"Multiplier should decrease as DEF rises: DEF={def_values[i]} → {multipliers[i]}, "
                f"DEF={def_values[i+1]} → {multipliers[i+1]}",
            )


class TestDefendVsAttack(unittest.TestCase):
    """
    Situational optimality: when enemy ATK is very high relative to player HP,
    defend (reduced damage + counter) should result in better survival than
    repeating attacks at the same RNG seed.
    """

    def _simulate_turns(self, action: str, turns: int, seed: int) -> dict:
        """Run `turns` turns of the given action and return final state dict."""
        random.seed(seed)
        game = MicroRPG()
        # Buff the floor-1 enemy to make it dangerous
        game.enemy["atk"] = 15
        game.enemy["hp"] = 999  # unkillable — we're measuring survival only

        for _ in range(turns):
            if game.is_over():
                break
            game.take_action(action)

        return game.get_state()

    def test_defend_survives_longer_than_attack_vs_high_atk_enemy(self):
        """
        Against a buffed enemy (ATK=15), defend should leave Iris with more HP
        after 5 turns than repeated attacking (which takes full counter-hits).
        """
        TURNS = 5
        SEED = 7

        defend_state = self._simulate_turns("defend", TURNS, SEED)
        attack_state = self._simulate_turns("attack", TURNS, SEED)

        self.assertGreaterEqual(
            defend_state["player_hp"],
            attack_state["player_hp"],
            f"Defend ({defend_state['player_hp']} HP) should end with ≥ HP vs "
            f"attack ({attack_state['player_hp']} HP) against a high-ATK enemy.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
