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
import queue
import threading
import atexit
from datetime import datetime
from typing import Any, Optional

from core.logger import log_system

TELEMETRY_DIR = os.path.join("logs", "balatro")
_TELEMETRY_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=5000)
_WRITER_THREAD: Optional[threading.Thread] = None
_WRITER_LOCK = threading.Lock()
_EXIT_HOOK_REGISTERED = False


def _ensure_writer_thread() -> None:
    global _WRITER_THREAD, _EXIT_HOOK_REGISTERED
    with _WRITER_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return

        def _worker() -> None:
            while True:
                path, record = _TELEMETRY_QUEUE.get()
                try:
                    os.makedirs(TELEMETRY_DIR, exist_ok=True)
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")
                except Exception as e:
                    log_system(f"[Balatro Telemetry] Failed async write: {e}")
                finally:
                    _TELEMETRY_QUEUE.task_done()

        _WRITER_THREAD = threading.Thread(
            target=_worker,
            name="balatro-telemetry-writer",
            daemon=True,
        )
        _WRITER_THREAD.start()
        if not _EXIT_HOOK_REGISTERED:
            atexit.register(flush_telemetry)
            _EXIT_HOOK_REGISTERED = True


def _enqueue_record(record: dict[str, Any]) -> None:
    _ensure_writer_thread()
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(TELEMETRY_DIR, f"{date_str}.jsonl")
    try:
        _TELEMETRY_QUEUE.put_nowait((path, record))
    except queue.Full:
        log_system("[Balatro Telemetry] Queue full; dropping telemetry record.")


def flush_telemetry(timeout_s: float = 2.0) -> None:
    """Best-effort queue flush used during graceful shutdown."""
    try:
        _TELEMETRY_QUEUE.join()
    except Exception as e:
        log_system(f"[Balatro Telemetry] Flush failed: {e}")


def log_decision(
    action: str,
    api_result: bool,
    error: Optional[str],
    raw_state: dict,
    planner_meta: Optional[dict[str, Any]] = None,
) -> None:
    """Write a per-turn decision record for gameplay analysis.

    Args:
        action: The planned action (e.g., "play_hand", "buy_shop", "discard").
        api_result: Whether the API call succeeded (True) or failed (False).
        error: Error message string if API call failed, None otherwise.
        raw_state: The raw game state dict from GameController.raw_state at decision time.
    """
    try:
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

        planner_meta = planner_meta or {}

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
            "planner_phase": planner_meta.get("phase_id", ""),
            "planner_allowed_actions": planner_meta.get("allowed_actions", []),
            "planner_raw_action": planner_meta.get("raw_action", ""),
            "planner_normalized_action": planner_meta.get("normalized_action", ""),
            "planner_fallback_applied": bool(
                planner_meta.get("fallback_applied", False)
            ),
            "planner_fallback_reason": planner_meta.get("fallback_reason", ""),
        }

        _enqueue_record(record)

    except Exception as e:
        log_system(f"[Balatro Telemetry] Failed to write decision log: {e}")


def log_death(raw_state: dict) -> None:
    """Write a structured death record on game over.

    Args:
        raw_state: The last raw game state dict from GameController.raw_state.
    """
    try:
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

        _enqueue_record(record)

        log_system(
            f"[Balatro Telemetry] Death logged → "
            f"Ante {record['ante_reached']}, "
            f"Boss: {record['boss_blind']}, "
            f"Money: ${record['final_money']}, "
            f"Jokers: {record['jokers']}"
        )

    except Exception as e:
        log_system(f"[Balatro Telemetry] Failed to write death log: {e}")


def log_survival_gate_blocked(
    raw_state: dict,
    target_joker: str,
    sacrificed_joker: str,
    current_immediate_score: int,
    new_immediate_score: int,
    gated_requirement: int,
    new_future_score: int,
    reason: str = "",
) -> None:
    """Write a structured event when a survival gate blocks a pivot."""
    try:
        blinds = raw_state.get("blinds") or {}
        boss = blinds.get("boss") if isinstance(blinds, dict) else {}
        if not isinstance(boss, dict):
            boss = {}

        record = {
            "event": "survival_gate_blocked",
            "timestamp": datetime.now().isoformat(),
            "ante": raw_state.get("ante_num", "?"),
            "money": raw_state.get("money", "?"),
            "state": raw_state.get("state", ""),
            "boss_blind": boss.get("name", ""),
            "target_joker": target_joker,
            "sacrificed_joker": sacrificed_joker,
            "current_immediate_score": int(current_immediate_score),
            "new_immediate_score": int(new_immediate_score),
            "gated_requirement": int(gated_requirement),
            "new_future_score": int(new_future_score),
            "reason": reason,
        }

        _enqueue_record(record)

    except Exception as e:
        log_system(f"[Balatro Telemetry] Failed to write survival gate log: {e}")


def log_score_anomaly(
    raw_state: dict,
    estimated_score: int,
    actual_score: int,
    delta_pct: float,
    hand_indices: list[int],
    hand_name: str,
) -> None:
    """Log estimator-vs-actual drift events for model desync detection."""
    try:
        blinds = raw_state.get("blinds") or {}
        boss = blinds.get("boss") if isinstance(blinds, dict) else {}
        if not isinstance(boss, dict):
            boss = {}
        record = {
            "event": "score_anomaly",
            "timestamp": datetime.now().isoformat(),
            "ante": raw_state.get("ante_num", "?"),
            "state": raw_state.get("state", ""),
            "boss_blind": boss.get("name", ""),
            "estimated_score": int(estimated_score),
            "actual_score": int(actual_score),
            "delta_pct": round(float(delta_pct), 2),
            "hand_indices": hand_indices,
            "hand_name": hand_name,
        }
        _enqueue_record(record)
    except Exception as e:
        log_system(f"[Balatro Telemetry] Failed to write anomaly log: {e}")
