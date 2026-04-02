"""
balatro_telemetry.py

Structured analytics logger for Iris's Balatro runs.
Writes one JSONL record per event to logs/balatro/<date>.jsonl.
- "decision" events: per-turn action records for gameplay analysis
- "death" events: game-over records for loss analysis

Reviewing these records reveals which boss blinds, ante levels, and
Joker loadouts are killing Iris most often, directly informing prompt tuning.
"""

import json
import os
from datetime import datetime
from typing import Any, Optional

from core.logger import log_system

TELEMETRY_DIR = os.path.join("logs", "balatro")


def log_decision(
    action: str,
    api_result: bool,
    error: Optional[str],
    raw_state: dict,
) -> None:
    """Write a per-turn decision record for gameplay analysis.

    Args:
        action: The planned action (e.g., "play_hand", "buy_shop", "discard").
        api_result: Whether the API call succeeded (True) or failed (False).
        error: Error message string if API call failed, None otherwise.
        raw_state: The raw game state dict from GameController.raw_state at decision time.
    """
    try:
        os.makedirs(TELEMETRY_DIR, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(TELEMETRY_DIR, f"{date_str}.jsonl")

        joker_container = raw_state.get("jokers") or {}
        if isinstance(joker_container, dict):
            joker_cards = joker_container.get("cards", [])
        else:
            joker_cards = []
        joker_names = [j.get("label", "?") for j in joker_cards if isinstance(j, dict)]

        blinds = raw_state.get("blinds") or {}
        if isinstance(blinds, dict):
            boss = blinds.get("boss") or {}
        else:
            boss = {}

        round_info = raw_state.get("round") or {}
        if isinstance(round_info, dict):
            hands_left = round_info.get("hands_left", 0)
            discards_left = round_info.get("discards_left", 0)
        else:
            hands_left = 0
            discards_left = 0

        record: dict[str, Any] = {
            "event": "decision",
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "api_success": api_result,
            "error": error,
            "ante": raw_state.get("ante_num", "?"),
            "money": raw_state.get("money", "?"),
            "hands_left": hands_left,
            "discards_left": discards_left,
            "jokers": joker_names,
            "boss_blind": boss.get("name", "") if isinstance(boss, dict) else "",
            "state": raw_state.get("state", ""),
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    except Exception as e:
        log_system(f"[Balatro Telemetry] Failed to write decision log: {e}")


def log_death(raw_state: dict) -> None:
    """Write a structured death record on game over.

    Args:
        raw_state: The last raw game state dict from GameController.raw_state.
    """
    try:
        os.makedirs(TELEMETRY_DIR, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(TELEMETRY_DIR, f"{date_str}.jsonl")

        # Extract joker names safely
        joker_container = raw_state.get("jokers") or {}
        if isinstance(joker_container, dict):
            joker_cards = joker_container.get("cards", [])
        else:
            joker_cards = []
        joker_names = [j.get("label", "?") for j in joker_cards if isinstance(j, dict)]

        # Extract boss blind info safely
        blinds = raw_state.get("blinds") or {}
        if isinstance(blinds, dict):
            boss = blinds.get("boss") or {}
        else:
            boss = {}

        # Extract round info safely
        round_info = raw_state.get("round") or {}
        if isinstance(round_info, dict):
            hands_left = round_info.get("hands_left", "?")
        else:
            hands_left = "?"

        record = {
            "event": "death",
            "timestamp": datetime.now().isoformat(),
            "ante_reached": raw_state.get("ante_num", "?"),
            "final_money": raw_state.get("money", "?"),
            "jokers": joker_names,
            "boss_blind": boss.get("name", "Unknown")
            if isinstance(boss, dict)
            else "Unknown",
            "boss_effect": boss.get("effect", "") if isinstance(boss, dict) else "",
            "boss_score_required": boss.get("score", "?")
            if isinstance(boss, dict)
            else "?",
            "hands_left_on_death": hands_left,
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        log_system(
            f"[Balatro Telemetry] Death logged → "
            f"Ante {record['ante_reached']}, "
            f"Boss: {record['boss_blind']}, "
            f"Money: ${record['final_money']}, "
            f"Jokers: {record['jokers']}"
        )

    except Exception as e:
        log_system(f"[Balatro Telemetry] Failed to write death log: {e}")
