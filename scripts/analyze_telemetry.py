"""analyze_telemetry.py — Balatro Iris Performance Dashboard.

Usage:
    python scripts/analyze_telemetry.py

Reads all .jsonl files in logs/balatro/ and computes run + operations statistics.
Corrupted lines and missing-field rows are silently skipped so that
disk I/O errors never pollute the metric averages.
"""

import collections
import json
import os
from typing import Iterable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve paths relative to this script so it works from any CWD.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
TELEMETRY_DIR = os.path.join(_PROJECT_ROOT, "logs", "balatro")

WIN_ANTE_THRESHOLD = 8  # Ante at which a run is counted as a win
TOP_N = 5  # How many entries to show per ranked list


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------


def _iter_records(telemetry_dir: str) -> Iterable[dict]:
    """Yield parsed dicts from every valid .jsonl line in *telemetry_dir*.

    Silently skips:
    - Non-files (subdirectories, symlinks)
    - Non-.jsonl extensions
    - Lines that fail JSON parsing (truncated hard-kill writes)
    - Rows that are not JSON objects
    """
    if not os.path.isdir(telemetry_dir):
        return

    for filename in sorted(os.listdir(telemetry_dir)):
        # Strict file-type filter
        if not filename.endswith(".jsonl"):
            continue
        full_path = os.path.join(telemetry_dir, filename)
        if not os.path.isfile(full_path):
            continue

        with open(full_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # Truncated line from a hard-killed session — skip cleanly.
                    continue

                if not isinstance(record, dict):
                    continue

                yield record


# ---------------------------------------------------------------------------
# Metric Computation
# ---------------------------------------------------------------------------


def compute_metrics(telemetry_dir: str) -> dict:
    """Return a dict of all computed run statistics."""
    total_runs = 0
    wins = 0
    total_antes = 0
    decision_events = 0
    failed_decisions = 0
    anomaly_events = 0
    anomaly_delta_total = 0.0
    gate_block_events = 0
    boss_counter: collections.Counter = collections.Counter()
    joker_counter: collections.Counter = collections.Counter()
    anomaly_hand_counter: collections.Counter = collections.Counter()
    gate_reason_counter: collections.Counter = collections.Counter()
    gate_target_counter: collections.Counter = collections.Counter()

    for record in _iter_records(telemetry_dir):
        event = str(record.get("event") or "").strip().lower()

        if event == "death":
            ante = record.get("ante_reached")
            if isinstance(ante, (int, float)):
                total_runs += 1
                total_antes += int(ante)

                if int(ante) >= WIN_ANTE_THRESHOLD:
                    wins += 1

                boss = record.get("boss_blind")
                if boss:
                    boss_counter[boss] += 1

                for joker_name in record.get("jokers") or []:
                    if joker_name:
                        joker_counter[joker_name] += 1
            continue

        if event == "decision":
            decision_events += 1
            if not bool(record.get("api_success", False)):
                failed_decisions += 1
            continue

        if event == "score_anomaly":
            anomaly_events += 1
            delta_pct = record.get("delta_pct")
            if isinstance(delta_pct, (int, float)):
                anomaly_delta_total += float(delta_pct)
            hand_name = str(record.get("hand_name") or "").strip()
            if hand_name:
                anomaly_hand_counter[hand_name] += 1
            continue

        if event == "survival_gate_blocked":
            gate_block_events += 1
            reason = str(record.get("reason") or "").strip()
            target = str(record.get("target_joker") or "").strip()
            if reason:
                gate_reason_counter[reason] += 1
            if target:
                gate_target_counter[target] += 1
            continue

    # Guard against empty / fully-corrupted log directories
    if total_runs > 0:
        avg_ante = total_antes / total_runs
        win_rate = (wins / total_runs) * 100.0
    else:
        avg_ante = None
        win_rate = None

    if anomaly_events > 0:
        avg_anomaly_delta = anomaly_delta_total / anomaly_events
    else:
        avg_anomaly_delta = None

    if decision_events > 0:
        decision_fail_rate = (failed_decisions / decision_events) * 100.0
    else:
        decision_fail_rate = None

    return {
        "total_runs": total_runs,
        "wins": wins,
        "win_rate": win_rate,
        "avg_ante": avg_ante,
        "decision_events": decision_events,
        "failed_decisions": failed_decisions,
        "decision_fail_rate": decision_fail_rate,
        "anomaly_events": anomaly_events,
        "avg_anomaly_delta": avg_anomaly_delta,
        "gate_block_events": gate_block_events,
        "top_bosses": boss_counter.most_common(TOP_N),
        "top_jokers": joker_counter.most_common(TOP_N),
        "top_anomaly_hands": anomaly_hand_counter.most_common(TOP_N),
        "top_gate_reasons": gate_reason_counter.most_common(TOP_N),
        "top_gate_targets": gate_target_counter.most_common(TOP_N),
    }


# ---------------------------------------------------------------------------
# Terminal Output
# ---------------------------------------------------------------------------


def print_dashboard(metrics: dict) -> None:
    """Print a formatted markdown-style dashboard to stdout."""
    width = 45
    bar = "=" * width

    print(bar)
    print("       BALATRO TELEMETRY DASHBOARD")
    print(bar)

    total = metrics["total_runs"]
    print(f"Total Runs Analyzed: {total}")

    if metrics["win_rate"] is None:
        print("Overall Win Rate:    N/A (no valid runs found)")
        print("Average Ante:        N/A")
    else:
        print(
            f"Overall Win Rate:    {metrics['win_rate']:.1f}%"
            f" ({metrics['wins']} Win{'s' if metrics['wins'] != 1 else ''})"
        )
        print(f"Average Ante:        {metrics['avg_ante']:.1f}")

    print()
    print("--- OPERATIONAL HEALTH ---")
    decisions = metrics["decision_events"]
    failures = metrics["failed_decisions"]
    print(f"Decision Events:      {decisions}")
    print(f"Failed Decisions:     {failures}")
    if metrics["decision_fail_rate"] is None:
        print("Decision Fail Rate:   N/A")
    else:
        print(f"Decision Fail Rate:   {metrics['decision_fail_rate']:.1f}%")
    print(f"Score Anomalies:      {metrics['anomaly_events']}")
    if metrics["avg_anomaly_delta"] is None:
        print("Avg Anomaly Drift:    N/A")
    else:
        print(f"Avg Anomaly Drift:    {metrics['avg_anomaly_delta']:.1f}%")
    print(f"Survival Gate Blocks: {metrics['gate_block_events']}")

    print()
    print("--- TOP ANOMALY HANDS ---")
    if metrics["top_anomaly_hands"]:
        for rank, (name, count) in enumerate(metrics["top_anomaly_hands"], start=1):
            print(
                f"{rank:>2}. {name:<28} ({count} anomaly{'ies' if count != 1 else ''})"
            )
    else:
        print("    No anomaly events recorded yet.")

    print()
    print("--- SURVIVAL GATE BLOCK REASONS ---")
    if metrics["top_gate_reasons"]:
        for rank, (name, count) in enumerate(metrics["top_gate_reasons"], start=1):
            print(f"{rank:>2}. {name:<28} ({count} event{'s' if count != 1 else ''})")
    else:
        print("    No survival gate block reasons recorded yet.")

    print()
    print("--- MOST BLOCKED TARGET JOKERS ---")
    if metrics["top_gate_targets"]:
        for rank, (name, count) in enumerate(metrics["top_gate_targets"], start=1):
            print(f"{rank:>2}. {name:<28} ({count} block{'s' if count != 1 else ''})")
    else:
        print("    No blocked target jokers recorded yet.")

    print()
    print("--- LETHAL BOSS BLINDS ---")
    if metrics["top_bosses"]:
        for rank, (name, count) in enumerate(metrics["top_bosses"], start=1):
            print(f"{rank:>2}. {name:<28} ({count} death{'s' if count != 1 else ''})")
    else:
        print("    No boss data recorded yet.")

    print()
    print("--- MOST COMMON JOKERS ON DEATH ---")
    if metrics["top_jokers"]:
        for rank, (name, count) in enumerate(metrics["top_jokers"], start=1):
            print(f"{rank:>2}. {name:<28} ({count} time{'s' if count != 1 else ''})")
    else:
        print("    No joker data recorded yet.")

    print(bar)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    metrics = compute_metrics(TELEMETRY_DIR)
    print_dashboard(metrics)
