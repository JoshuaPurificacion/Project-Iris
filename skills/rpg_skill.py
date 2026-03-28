"""
skills/rpg_skill.py

Bridge between MicroRPG and Iris.
  format_prompt(state) → str   : injects game state into Iris's context
  parse_action(text)   → str   : extracts "attack" | "flee" | "use item" from Iris's free-form speech
"""

import re

VALID_ACTIONS = ["attack", "flee", "use item"]

# Keywords that map to each action
ACTION_KEYWORDS = {
    "attack": [
        "attack", "strike", "hit", "fight", "slash", "stab", "swing",
        "charge", "punch", "smash", "go for it", "kill", "destroy",
        "offensive", "engage", "take it down", "take them down",
    ],
    "flee": [
        "flee", "run", "escape", "retreat", "back off", "get out",
        "leave", "bail", "withdraw", "avoid", "hide",
    ],
    "use item": [
        "use item", "potion", "heal", "drink", "use potion",
        "use it", "recover", "restore", "chug", "take potion",
        "use my potion", "use the potion",
    ],
}


def format_prompt(state: dict) -> str:
    """
    Build the context string injected before Iris generates her turn decision.
    Keeps it short — she only needs the essentials to make a smart decision.
    """
    p_hp    = state["player_hp"]
    p_max   = state["player_max_hp"]
    hp_pct  = int((p_hp / p_max) * 100) if p_max > 0 else 0

    e_name  = state.get("enemy_name", "enemy")
    e_hp    = state.get("enemy_hp", 0)
    e_max   = state.get("enemy_max_hp", 1)
    e_pct   = int((e_hp / e_max) * 100) if e_max > 0 else 0

    wpn     = state["weapon_name"]
    wpn_atk = state["weapon_atk"]
    has_pot = state["has_potion"]
    potions = state["potions"]
    gold    = state["player_gold"]
    floor   = state["floor"]
    room    = state["room_name"]
    status  = state["status"]

    # Game-over states
    if status == "victory":
        return (
            "You just defeated the final boss and won the dungeon! "
            "React with pure excitement — you conquered the Dragon's Lair. "
            "Keep it to 1-2 sentences."
        )
    if status == "defeat":
        return (
            "You just died. React with genuine disappointment — "
            "Iris fell in battle. Keep it to 1-2 sentences."
        )
    if status == "escaped":
        return (
            "You just fled the dungeon. React with a mix of relief and mild shame. "
            "Keep it to 1-2 sentences."
        )

    # Recent events for context
    recent = state.get("recent_log", [])
    recent_str = " | ".join(recent[-3:]) if recent else "none"

    # Low HP warning
    low_hp_note = ""
    if hp_pct <= 25:
        low_hp_note = " WARNING: critically low HP — consider using a potion if available."
    elif hp_pct <= 50:
        low_hp_note = " HP is below half — be cautious."

    potion_note = f"You have potions: {potions}." if has_pot else "No potions left."

    prompt = (
        f"[GAME STATE — Floor {floor}: {room}] "
        f"Your HP: {p_hp}/{p_max} ({hp_pct}%).{low_hp_note} "
        f"Weapon: {wpn} (ATK {wpn_atk}). "
        f"{potion_note} "
        f"Gold: {gold}. "
        f"Enemy: {e_name} — HP {e_hp}/{e_max} ({e_pct}%). "
        f"Recent events: {recent_str}. "
        f"Choose your action: attack, flee, or use item. "
        f"Respond in 1-2 sentences in Iris's voice, then clearly state your choice."
    )
    return prompt


def parse_action(text: str) -> str:
    """
    Extract a valid action from Iris's free-form response.
    Returns "attack" | "flee" | "use item".
    Falls back to "attack" if nothing matches.
    """
    text_lower = text.lower()

    # Direct match first (highest confidence)
    for action in VALID_ACTIONS:
        if action in text_lower:
            return action

    # Keyword fuzzy match
    scores = {action: 0 for action in VALID_ACTIONS}
    for action, keywords in ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[action] += 1

    best = max(scores, key=lambda a: scores[a])
    if scores[best] > 0:
        return best

    # Default fallback
    return "attack"
