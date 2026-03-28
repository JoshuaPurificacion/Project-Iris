"""
games/micro_rpg.py

Self-contained micro RPG engine for Iris.
Pokémon-style: turn-based combat, RNG loot with stat rolls,
4 floors with escalating difficulty and unique loot tables.

Public API (used by rpg_skill.py):
    game = MicroRPG()
    state = game.get_state()   → dict
    result = game.take_action("attack" | "flee" | "use item")  → dict
    game.is_over()             → bool
"""

import random

# ─────────────────────────────────────────────────────────────
#  ITEM DEFINITIONS
# ─────────────────────────────────────────────────────────────

WEAPONS = {
    # name: (base_atk, description)
    "Rusty Dagger":    (3, "a dull blade with a cracked handle"),
    "Iron Dagger":     (5, "a reliable short blade"),
    "Steel Sword":     (8, "a well-balanced sword"),
    "War Axe":         (11, "a heavy axe with brutal momentum"),
    "Elven Blade":     (14, "a razor-sharp blade etched with runes"),
    "Dragon Fang":     (18, "a blade carved from a dragon's own fang"),
}

POTIONS = {
    "Small Potion":  6,
    "Large Potion":  14,
    "Mega Potion":   25,
}

# ─────────────────────────────────────────────────────────────
#  FLOOR / ROOM DEFINITIONS
# ─────────────────────────────────────────────────────────────

FLOORS = [
    {
        "floor": 1,
        "name": "Entrance Hall",
        "description": "Crumbling stone walls. Torchlight flickers. Something scurries in the dark.",
        "enemies": [
            {"name": "Goblin",    "hp": 10, "max_hp": 10, "atk": 3, "def": 0, "gold": (2, 5)},
            {"name": "Giant Rat", "hp": 7,  "max_hp": 7,  "atk": 2, "def": 0, "gold": (1, 3)},
        ],
        "loot_table": [
            ("Iron Dagger",   0.20),
            ("Small Potion",  0.45),
            ("gold",          0.35),
        ],
        "gold_range": (3, 8),
    },
    {
        "floor": 2,
        "name": "Dark Forest",
        "description": "Twisted trees form a canopy overhead. Bones litter the path.",
        "enemies": [
            {"name": "Skeleton", "hp": 14, "max_hp": 14, "atk": 5, "def": 1, "gold": (4, 8)},
            {"name": "Forest Wolf", "hp": 12, "max_hp": 12, "atk": 6, "def": 0, "gold": (3, 6)},
        ],
        "loot_table": [
            ("Steel Sword",   0.15),
            ("Iron Dagger",   0.15),
            ("Small Potion",  0.30),
            ("Large Potion",  0.20),
            ("gold",          0.20),
        ],
        "gold_range": (6, 14),
    },
    {
        "floor": 3,
        "name": "Ancient Ruins",
        "description": "Collapsed pillars and faded murals. Magic crackles in the air.",
        "enemies": [
            {"name": "Orc Warrior", "hp": 20, "max_hp": 20, "atk": 7, "def": 2, "gold": (8, 14)},
            {"name": "Dark Mage",   "hp": 15, "max_hp": 15, "atk": 10, "def": 1, "gold": (10, 18)},
        ],
        "loot_table": [
            ("War Axe",      0.15),
            ("Steel Sword",  0.15),
            ("Large Potion", 0.25),
            ("Mega Potion",  0.10),
            ("Elven Blade",  0.10),
            ("gold",         0.25),
        ],
        "gold_range": (12, 22),
    },
    {
        "floor": 4,
        "name": "Dragon's Lair",
        "description": "Scorched walls. Mountains of gold. A massive shadow stirs ahead.",
        "enemies": [
            {"name": "Cave Dragon", "hp": 40, "max_hp": 40, "atk": 12, "def": 4, "gold": (30, 50)},
        ],
        "loot_table": [
            ("Dragon Fang",  0.50),
            ("Elven Blade",  0.20),
            ("Mega Potion",  0.30),
        ],
        "gold_range": (30, 50),
    },
]

# ─────────────────────────────────────────────────────────────
#  RNG HELPERS
# ─────────────────────────────────────────────────────────────

def _roll_weapon_stats(weapon_name):
    """Roll a forge bonus of ±2 on a weapon's base ATK."""
    base_atk, desc = WEAPONS[weapon_name]
    forge_bonus = random.randint(-2, 2)
    final_atk = max(1, base_atk + forge_bonus)
    return {"name": weapon_name, "atk": final_atk, "forge": forge_bonus, "desc": desc}

def _roll_loot(loot_table, gold_range):
    """Roll the loot table and return a loot dict or None."""
    roll = random.random()
    cumulative = 0.0
    for item_name, chance in loot_table:
        cumulative += chance
        if roll < cumulative:
            if item_name == "gold":
                amount = random.randint(*gold_range)
                return {"type": "gold", "amount": amount}
            elif item_name in POTIONS:
                return {"type": "potion", "name": item_name, "heal": POTIONS[item_name]}
            elif item_name in WEAPONS:
                return {"type": "weapon", "item": _roll_weapon_stats(item_name)}
    return None

# ─────────────────────────────────────────────────────────────
#  GAME ENGINE
# ─────────────────────────────────────────────────────────────

PLAYER_BASE_HP   = 30
PLAYER_BASE_ATK  = 4
PLAYER_BASE_DEF  = 1
MISS_CHANCE      = 0.15
CRIT_CHANCE      = 0.10
CRIT_MULTIPLIER  = 1.5

class MicroRPG:
    def __init__(self):
        self.player = {
            "hp":         PLAYER_BASE_HP,
            "max_hp":     PLAYER_BASE_HP,
            "def":        PLAYER_BASE_DEF,
            "gold":       0,
            "weapon":     _roll_weapon_stats("Rusty Dagger"),
            "potions":    {},   # name → count
        }
        self.floor_index  = 0
        self.floor_data   = FLOORS[self.floor_index]
        self.enemy        = self._spawn_enemy()
        self.turn         = "player"   # "player" | "enemy"
        self.log          = []         # list of event strings
        self.status       = "active"   # "active" | "victory" | "defeat" | "escaped"
        self.rooms_cleared = 0
        self._log(f"You enter the {self.floor_data['name']}.")
        self._log(self.floor_data["description"])

    # ── Internal helpers ──────────────────────────────────────

    def _spawn_enemy(self):
        template = random.choice(self.floor_data["enemies"])
        import copy
        e = copy.deepcopy(template)
        return e

    def _log(self, msg):
        self.log.append(msg)
        if len(self.log) > 30:
            self.log.pop(0)

    def _player_atk(self):
        return self.player["weapon"]["atk"]

    def _resolve_player_attack(self):
        events = []
        if random.random() < MISS_CHANCE:
            events.append("Iris swings — but misses!")
            return events, 0

        dmg = max(1, self._player_atk() + random.randint(0, 2) - self.enemy.get("def", 0))
        is_crit = random.random() < CRIT_CHANCE
        if is_crit:
            dmg = int(dmg * CRIT_MULTIPLIER)
            events.append(f"CRITICAL HIT! Iris strikes {self.enemy['name']} for {dmg} damage!")
        else:
            events.append(f"Iris attacks {self.enemy['name']} for {dmg} damage.")
        self.enemy["hp"] = max(0, self.enemy["hp"] - dmg)
        return events, dmg

    def _resolve_enemy_attack(self):
        events = []
        if random.random() < MISS_CHANCE:
            events.append(f"{self.enemy['name']} attacks — but misses!")
            return events, 0

        dmg = max(1, self.enemy["atk"] + random.randint(0, 1) - self.player["def"])
        events.append(f"{self.enemy['name']} retaliates for {dmg} damage.")
        self.player["hp"] = max(0, self.player["hp"] - dmg)
        return events, dmg

    def _handle_kill(self):
        events = []
        events.append(f"{self.enemy['name']} is defeated!")

        # Gold from enemy
        gold_drop = random.randint(*self.enemy["gold"])
        self.player["gold"] += gold_drop
        events.append(f"Gained {gold_drop} gold.")

        # Loot roll
        loot = _roll_loot(self.floor_data["loot_table"], self.floor_data["gold_range"])
        if loot:
            if loot["type"] == "gold":
                self.player["gold"] += loot["amount"]
                events.append(f"Found {loot['amount']} gold in the remains!")
            elif loot["type"] == "potion":
                name = loot["name"]
                self.player["potions"][name] = self.player["potions"].get(name, 0) + 1
                events.append(f"Looted a {name}!")
            elif loot["type"] == "weapon":
                w = loot["item"]
                forge_str = f"+{w['forge']}" if w["forge"] >= 0 else str(w["forge"])
                events.append(f"Looted {w['name']} (ATK {w['atk']}, forge {forge_str})!")
                # Auto-equip if better
                if w["atk"] > self.player["weapon"]["atk"]:
                    old = self.player["weapon"]["name"]
                    self.player["weapon"] = w
                    events.append(f"Equipped {w['name']} (upgrade from {old}).")

        self.rooms_cleared += 1

        # Advance floor or end game
        if self.floor_index >= len(FLOORS) - 1:
            self.status = "victory"
            events.append("You have conquered the Dragon's Lair! VICTORY!")
        else:
            self.floor_index += 1
            self.floor_data = FLOORS[self.floor_index]
            self.enemy = self._spawn_enemy()
            events.append(f"You descend to Floor {self.floor_index + 1}: {self.floor_data['name']}.")
            events.append(self.floor_data["description"])

        return events

    # ── Public API ────────────────────────────────────────────

    def get_state(self):
        """Return a clean snapshot dict for Iris's prompt and the UI."""
        potion_summary = ", ".join(
            f"{name} x{count}" for name, count in self.player["potions"].items()
        ) or "none"

        weapon = self.player["weapon"]
        forge_str = f"+{weapon['forge']}" if weapon["forge"] >= 0 else str(weapon["forge"])

        has_potion = any(v > 0 for v in self.player["potions"].values())

        return {
            "status":        self.status,
            "floor":         self.floor_index + 1,
            "room_name":     self.floor_data["name"],
            "room_desc":     self.floor_data["description"],
            "player_hp":     self.player["hp"],
            "player_max_hp": self.player["max_hp"],
            "player_def":    self.player["def"],
            "player_gold":   self.player["gold"],
            "weapon_name":   weapon["name"],
            "weapon_atk":    weapon["atk"],
            "weapon_forge":  forge_str,
            "potions":       potion_summary,
            "has_potion":    has_potion,
            "enemy_name":    self.enemy["name"] if self.enemy else None,
            "enemy_hp":      self.enemy["hp"] if self.enemy else 0,
            "enemy_max_hp":  self.enemy["max_hp"] if self.enemy else 0,
            "enemy_atk":     self.enemy["atk"] if self.enemy else 0,
            "rooms_cleared": self.rooms_cleared,
            "recent_log":    self.log[-6:],
            "full_log":      list(self.log),
        }

    def take_action(self, action: str) -> dict:
        """
        Process one player action. Returns a result dict with:
            events   : list of narrative strings
            loot     : loot dict or None
            state    : updated get_state() snapshot
        """
        if self.status != "active":
            return {"events": ["The battle is already over."], "loot": None, "state": self.get_state()}

        action = action.lower().strip()
        events = []

        # ── ATTACK ──────────────────────────────────────────
        if action == "attack":
            atk_events, dmg = self._resolve_player_attack()
            events.extend(atk_events)

            if self.enemy["hp"] <= 0:
                kill_events = self._handle_kill()
                events.extend(kill_events)
            else:
                # Enemy counter-attack
                enemy_events, _ = self._resolve_enemy_attack()
                events.extend(enemy_events)
                if self.player["hp"] <= 0:
                    self.status = "defeat"
                    events.append("Iris has fallen... GAME OVER.")

        # ── FLEE ────────────────────────────────────────────
        elif action == "flee":
            flee_chance = 0.5
            if random.random() < flee_chance:
                self.status = "escaped"
                events.append("Iris turns and flees! She escapes... but the dungeon remains unconquered.")
            else:
                events.append("Iris tries to flee — blocked! The enemy attacks!")
                enemy_events, _ = self._resolve_enemy_attack()
                events.extend(enemy_events)
                if self.player["hp"] <= 0:
                    self.status = "defeat"
                    events.append("Iris has fallen... GAME OVER.")

        # ── USE ITEM ────────────────────────────────────────
        elif action == "use item":
            potion_name = next(
                (n for n, c in self.player["potions"].items() if c > 0), None
            )
            if potion_name:
                heal = POTIONS[potion_name]
                old_hp = self.player["hp"]
                self.player["hp"] = min(self.player["max_hp"], self.player["hp"] + heal)
                actual_heal = self.player["hp"] - old_hp
                self.player["potions"][potion_name] -= 1
                events.append(f"Iris uses {potion_name} and recovers {actual_heal} HP.")

                # Enemy still attacks this turn
                enemy_events, _ = self._resolve_enemy_attack()
                events.extend(enemy_events)
                if self.player["hp"] <= 0:
                    self.status = "defeat"
                    events.append("Iris has fallen... GAME OVER.")
            else:
                events.append("No potions in inventory — Iris hesitates. The enemy attacks!")
                enemy_events, _ = self._resolve_enemy_attack()
                events.extend(enemy_events)
                if self.player["hp"] <= 0:
                    self.status = "defeat"
                    events.append("Iris has fallen... GAME OVER.")

        else:
            # Unknown action — default to attack
            events.append(f"(Defaulting to attack — unknown action: '{action}')")
            return self.take_action("attack")

        for e in events:
            self._log(e)

        return {"events": events, "state": self.get_state()}

    def is_over(self):
        return self.status in ("victory", "defeat", "escaped")
