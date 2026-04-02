"""analyze_telemetry.py — Balatro Iris Performance Dashboard.

Usage:
    python scripts/analyze_telemetry.py

Reads all .jsonl files in logs/balatro/ and computes run statistics.
Corrupted lines and missing-field rows are silently skipped so that
disk I/O errors never pollute the metric averages.
"""

import collections
import json
import os

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve paths relative to this script so it works from any CWD.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
TELEMETRY_DIR = os.path.join(_PROJECT_ROOT, "logs", "balatro")

WIN_ANTE_THRESHOLD = 8   # Ante at which a run is counted as a win
TOP_N = 5                # How many entries to show per ranked list


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def _iter_records(telemetry_dir: str):
    """Yield parsed dicts from every valid .jsonl line in *telemetry_dir*.

    Silently skips:
    - Non-files (subdirectories, symlinks)
    - Non-.jsonl extensions
    - Lines that fail JSON parsing (truncated hard-kill writes)
    - Lines that parse successfully but are missing 'ante_reached'
      (required for all aggregations to stay mathematically clean)
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

                # Skip rows missing core metrics rather than defaulting to 0,
                # which would silently corrupt Average Ante and Win Rate math.
                if record.get("ante_reached") is None:
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
    boss_counter: collections.Counter = collections.Counter()
    joker_counter: collections.Counter = collections.Counter()

    for record in _iter_records(telemetry_dir):
        total_runs += 1

        ante = record["ante_reached"]   # guaranteed non-None by _iter_records
        total_antes += ante

        if ante >= WIN_ANTE_THRESHOLD:
            wins += 1

        boss = record.get("boss_blind")
        if boss:
            boss_counter[boss] += 1

        for joker_name in record.get("jokers") or []:
            if joker_name:
                joker_counter[joker_name] += 1

    # Guard against empty / fully-corrupted log directories
    if total_runs > 0:
        avg_ante = total_antes / total_runs
        win_rate = (wins / total_runs) * 100.0
    else:
        avg_ante = None
        win_rate = None

    return {
        "total_runs": total_runs,
        "wins": wins,
        "win_rate": win_rate,
        "avg_ante": avg_ante,
        "top_bosses": boss_counter.most_common(TOP_N),
        "top_jokers": joker_counter.most_common(TOP_N),
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
