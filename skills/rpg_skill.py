"""
skills/rpg_skill.py

Bridge between MicroRPG and Iris.
  format_prompt(state) → str   : injects game state into Iris's context
  parse_action(text)   → str   : extracts "attack" | "flee" | "use item" from Iris's free-form speech
"""

import re

VALID_ACTIONS = ["attack", "defend", "heal", "flee"]

# Keywords that map to each action
ACTION_KEYWORDS = {
    "attack": [
        "attack",
        "strike",
        "hit",
        "fight",
        "slash",
        "stab",
        "swing",
        "charge",
        "punch",
        "smash",
        "go for it",
        "kill",
        "destroy",
        "offensive",
        "engage",
        "take it down",
        "take them down",
    ],
    "defend": [
        "defend",
        "block",
        "guard",
        "shield",
        "brace",
        "parry",
        "turtle",
        "hold",
        "protect",
    ],
    "heal": [
        "heal",
        "potion",
        "use item",
        "use potion",
        "drink",
        "recover",
        "restore",
        "chug",
        "take potion",
        "use my potion",
        "use the potion",
        "recover hp",
        "bandage",
    ],
    "flee": [
        "flee",
        "run",
        "escape",
        "retreat",
        "back off",
        "get out",
        "leave",
        "bail",
        "withdraw",
        "avoid",
        "hide",
    ],
}


def format_prompt(state: dict) -> str:
    """
    Build the context string injected before Iris generates her turn decision.
    Forces Iris to read the recent events log and act like a VTuber streamer.
    """
    # Game-over states
    status = state.get("status", "active")
    if status == "victory":
        return (
            "You just defeated the final boss and won the dungeon! "
            "React with pure excitement — you conquered the Dragon's Lair. "
            "Then, explicitly ask the chat if they want to see another run. Keep it to 1-2 sentences."
        )
    if status == "defeat":
        return (
            "You just died in the dungeon. React with genuine disappointment — "
            "you fell in battle. Then, explicitly ask the chat if they want you to try again. Keep it to 1-2 sentences."
        )
    if status == "escaped":
        return (
            "You just fled the dungeon. React with a mix of relief and mild shame. "
            "Then, explicitly ask the chat if they want you to try again. Keep it to 1-2 sentences."
        )

    recent_events = "\n".join(state.get("recent_log", [])[-3:]) or "No recent actions yet."

    prompt = f"""[STREAM DATA]
Location: {state['room_name']}
HP: {state['player_hp']}/{state['player_max_hp']} | Potions: {state['potions']}
Enemy: {state['enemy_name']} (HP: {state['enemy_hp']}/{state['enemy_max_hp']})

[LATEST ACTIONS]
{recent_events}

[INSTRUCTION]
React to the LATEST ACTIONS. If you hit a crit, celebrate! If the {state['enemy_name']} is low, talk trash.
Speak to Joshua/Chat. Use 2-3 sentences. Do NOT say the word 'attack' or 'defend'.
If you have no potions, do NOT choose [heal].
End with exactly one silent tag: [attack], [defend], [heal], or [flee]."""

    return prompt


def parse_action(text: str) -> str:
    """
    Extract a valid action from Iris's free-form response.
    Returns "attack" | "flee" | "use item".
    Falls back to "attack" if nothing matches.
    """
    text_lower = text.lower().strip()

    # Map legacy alias
    text_lower = text_lower.replace("use item", "heal")

    # Match bracketed exact commands first
    for action in VALID_ACTIONS:
        if f"[{action}]" in text_lower:
            return action

    # Legacy bracket alias
    if "[use item]" in text_lower:
        return "heal"

    # Direct match (highest confidence fallback)
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

    # Default fallback — keep visibility for debugging
    try:
        print(f"[rpg_skill] Fallback to attack (no keyword hit). Text: {text}")
    except Exception:
        pass
    return "attack"
