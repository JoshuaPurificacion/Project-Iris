"""
API Fuzzer — Consumable Targeting Discovery
===========================================
Hits the live BalatroBot server with every plausible RPC call shape to find
the correct method for selecting / targeting cards during the PLAY state.

Usage (server must be running):
    python scripts/test_api.py
    python scripts/test_api.py --host 127.0.0.1 --port 12346

Each probe prints:  [PASS] / [FAIL] / [ERROR]  with the raw response or error.
Stop the script at any time with Ctrl+C. Results are also written to:
    scripts/test_api_results.txt

Candidates tested
-----------------
GROUP A — Bare use (no select pre-call)
  A1. use {"consumable": C, "targets": [T...]}           ← might just work!
  A2. use {"consumable": C}                              ← baseline, no targets

GROUP B — toggle_highlight variants
  B1. toggle_highlight {"cards": [T...]}  then use
  B2. toggle_highlight {"card": T}  (single card, repeated)  then use

GROUP C — highlight variants
  C1. highlight {"cards": [T...]}  then use
  C2. highlight {"card": T}         then use

GROUP D — select_cards variants
  D1. select_cards {"cards": [T...]}  then use
  D2. select_cards {"card": T}        then use

GROUP E — select with different key names
  E1. select {"targets": [T...]}  then use
  E2. select {"hand": [T...]}     then use
  E3. select {"card": T}          then use (single)

GROUP F — use with alternate key shapes
  F1. use {"card": C, "targets": [T...]}          (card vs consumable key)
  F2. use {"consumable": C, "cards": [T...]}      (cards key instead of targets)
  F3. use {"consumable": C, "selected": [T...]}

NOTE: The script does NOT auto-confirm destructive actions; it reports pass/fail
based on whether the server returns a result (not None).  A PASS here means the
server accepted the call — you'll need to visually verify the in-game effect.
"""

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 12346
DEFAULT_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Minimal RPC client (no dependency on balatro_client.py intentionally)
# ---------------------------------------------------------------------------

_req_id = 1


def rpc(host: str, port: int, method: str, params: Optional[Dict] = None, timeout: float = DEFAULT_TIMEOUT):
    """Raw JSON-RPC 2.0 call. Returns (result_or_None, error_str_or_None)."""
    global _req_id
    payload = {"jsonrpc": "2.0", "method": method, "id": _req_id}
    if params:
        payload["params"] = params
    _req_id += 1
    try:
        r = requests.post(f"http://{host}:{port}/", json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return None, data["error"].get("message", str(data["error"]))
        return data.get("result"), None
    except requests.exceptions.Timeout:
        return None, "TIMEOUT"
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

class Probe:
    def __init__(self, tag: str, description: str, calls: List[Dict]):
        """
        calls: list of {"method": str, "params": dict} dicts executed in sequence.
               The probe PASSES if the LAST call returns a non-None result.
        """
        self.tag = tag
        self.description = description
        self.calls = calls

    def run(self, host: str, port: int) -> dict:
        results = []
        for step in self.calls:
            result, err = rpc(host, port, step["method"], step.get("params"))
            results.append({
                "method": step["method"],
                "params": step.get("params"),
                "result": result,
                "error": err,
            })
            time.sleep(0.15)  # small delay between chained calls

        final = results[-1]
        passed = final["result"] is not None
        return {"tag": self.tag, "description": self.description, "passed": passed, "steps": results}


def build_probes(consumable_idx: int, target_indices: List[int]) -> List[Probe]:
    C = consumable_idx
    T = target_indices

    return [
        # ── GROUP A ──────────────────────────────────────────────────────────
        Probe("A1", "use {consumable, targets} — no select pre-call", [
            {"method": "use", "params": {"consumable": C, "targets": T}},
        ]),
        Probe("A2", "use {consumable} — baseline, no targets at all", [
            {"method": "use", "params": {"consumable": C}},
        ]),

        # ── GROUP B ──────────────────────────────────────────────────────────
        Probe("B1", "toggle_highlight {cards:[...]} then use", [
            {"method": "toggle_highlight", "params": {"cards": T}},
            {"method": "use", "params": {"consumable": C}},
        ]),
        Probe("B2", "toggle_highlight {card:T} per card then use", [
            *[{"method": "toggle_highlight", "params": {"card": t}} for t in T],
            {"method": "use", "params": {"consumable": C}},
        ]),

        # ── GROUP C ──────────────────────────────────────────────────────────
        Probe("C1", "highlight {cards:[...]} then use", [
            {"method": "highlight", "params": {"cards": T}},
            {"method": "use", "params": {"consumable": C}},
        ]),
        Probe("C2", "highlight {card:T} per card then use", [
            *[{"method": "highlight", "params": {"card": t}} for t in T],
            {"method": "use", "params": {"consumable": C}},
        ]),

        # ── GROUP D ──────────────────────────────────────────────────────────
        Probe("D1", "select_cards {cards:[...]} then use", [
            {"method": "select_cards", "params": {"cards": T}},
            {"method": "use", "params": {"consumable": C}},
        ]),
        Probe("D2", "select_cards {card:T} per card then use", [
            *[{"method": "select_cards", "params": {"card": t}} for t in T],
            {"method": "use", "params": {"consumable": C}},
        ]),

        # ── GROUP E ──────────────────────────────────────────────────────────
        Probe("E1", "select {targets:[...]} then use", [
            {"method": "select", "params": {"targets": T}},
            {"method": "use", "params": {"consumable": C}},
        ]),
        Probe("E2", "select {hand:[...]} then use", [
            {"method": "select", "params": {"hand": T}},
            {"method": "use", "params": {"consumable": C}},
        ]),
        Probe("E3", "select {card:T} per card then use", [
            *[{"method": "select", "params": {"card": t}} for t in T],
            {"method": "use", "params": {"consumable": C}},
        ]),

        # ── GROUP F ──────────────────────────────────────────────────────────
        Probe("F1", "use {card:C, targets:[...]} — 'card' key instead of 'consumable'", [
            {"method": "use", "params": {"card": C, "targets": T}},
        ]),
        Probe("F2", "use {consumable:C, cards:[...]} — 'cards' key instead of 'targets'", [
            {"method": "use", "params": {"consumable": C, "cards": T}},
        ]),
        Probe("F3", "use {consumable:C, selected:[...]} — 'selected' key", [
            {"method": "use", "params": {"consumable": C, "selected": T}},
        ]),
    ]


# ---------------------------------------------------------------------------
# Inspect mode — read-only dump of consumables + hand card fields
# ---------------------------------------------------------------------------

def run_inspect(host: str, port: int) -> None:
    """Fetch live state and pretty-print every field on consumables and hand cards.

    Used to discover:
      - The exact key/format of a consumable's effect description string
        (so we know how to detect 'this Tarot applies Lucky enhancement')
      - The exact key/format of a hand card's existing enhancement field
        (so we know what to compare against)

    No game state is modified.
    """
    print(f"\n{'='*70}")
    print(f"  Balatro State Inspector")
    print(f"  Server : http://{host}:{port}/")
    print(f"{'='*70}\n")

    state, err = rpc(host, port, "gamestate")
    if state is None:
        print(f"[ERROR] Cannot reach server: {err}")
        sys.exit(1)

    current_phase = (state.get("state") or "unknown").upper()
    print(f"  Game state : {current_phase}\n")

    # ── Consumables ─────────────────────────────────────────────────────────
    consumeables = state.get("consumeables") or state.get("consumables") or {}
    consumable_cards = consumeables.get("cards", []) if isinstance(consumeables, dict) else []

    print(f"  CONSUMABLES ({len(consumable_cards)} found)")
    print(f"  {'─'*60}")
    if not consumable_cards:
        print("  (none in hand)")
    for i, card in enumerate(consumable_cards):
        print(f"  [{i}] Raw JSON:")
        print(json.dumps(card, indent=6))
        print()

    # ── Hand cards ───────────────────────────────────────────────────────────
    hand = state.get("hand") or {}
    hand_cards = hand.get("cards", []) if isinstance(hand, dict) else []

    print(f"  HAND CARDS ({len(hand_cards)} found)")
    print(f"  {'─'*60}")
    if not hand_cards:
        print("  (no hand — may not be in SELECTING_HAND / PLAY state)")
    for i, card in enumerate(hand_cards):
        print(f"  [{i}] Raw JSON:")
        print(json.dumps(card, indent=6))
        print()

    # ── Write to file ────────────────────────────────────────────────────────
    out_path = "scripts/test_api_inspect.txt"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"Balatro State Inspection\n")
            f.write(f"game_state={current_phase}\n\n")
            f.write("=== CONSUMABLES ===\n")
            f.write(json.dumps(consumable_cards, indent=2))
            f.write("\n\n=== HAND CARDS ===\n")
            f.write(json.dumps(hand_cards, indent=2))
            f.write("\n")
        print(f"  Full dump written to: {out_path}")
    except Exception as e:
        print(f"  (Could not write inspect file: {e})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def print_result(r: dict, verbose: bool) -> str:
    status = "[PASS]" if r["passed"] else "[FAIL]"
    line = f"{status} {r['tag']:4s}  {r['description']}"
    print(line)
    if verbose or r["passed"]:
        for step in r["steps"]:
            indent = "      "
            print(f"{indent}→ {step['method']}({json.dumps(step['params'])})")
            if step["error"]:
                print(f"{indent}  ✗ error: {step['error']}")
            else:
                print(f"{indent}  ✓ result: {json.dumps(step['result'])[:120]}")
    return line


def run_fuzzer(host: str, port: int, consumable_idx: int, target_indices: List[int], verbose: bool) -> None:
    print(f"\n{'='*70}")
    print(f"  Balatro API Fuzzer — Consumable Targeting Discovery")
    print(f"  Server : http://{host}:{port}/")
    print(f"  consumable_idx = {consumable_idx}  |  target_indices = {target_indices}")
    print(f"{'='*70}\n")

    # Sanity check — can we reach the server?
    state, err = rpc(host, port, "gamestate")
    if state is None:
        print(f"[ERROR] Cannot reach server: {err}")
        print("        Is Balatro running with the mod loaded?")
        sys.exit(1)

    current_phase = (state.get("state") or "unknown").upper()
    print(f"  Current game state: {current_phase}\n")

    if "PLAY" not in current_phase:
        print(f"  ⚠  WARNING: Game is in '{current_phase}', not PLAY.")
        print("     Targeted consumables should be tested in PLAY state.")
        print("     Results may differ. Proceeding anyway...\n")

    probes = build_probes(consumable_idx, target_indices)
    results = []
    passed_tags = []

    for probe in probes:
        r = probe.run(host, port)
        results.append(r)
        print_result(r, verbose)
        if r["passed"]:
            passed_tags.append(r["tag"])

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY: {len(passed_tags)}/{len(probes)} probes passed")
    if passed_tags:
        print(f"  ✓ PASSED: {', '.join(passed_tags)}")
    else:
        print("  ✗ No probes passed — server may not be in correct state,")
        print("    or consumable_idx / target_indices may be invalid.")
        print("    Re-run with --verbose for full error details.")
    print(f"{'='*70}\n")

    # Write results file
    results_path = "scripts/test_api_results.txt"
    try:
        with open(results_path, "w", encoding="utf-8") as f:
            f.write(f"Balatro API Fuzz Results\n")
            f.write(f"consumable_idx={consumable_idx}  target_indices={target_indices}\n")
            f.write(f"game_state={current_phase}\n\n")
            for r in results:
                status = "PASS" if r["passed"] else "FAIL"
                f.write(f"[{status}] {r['tag']} — {r['description']}\n")
                for step in r["steps"]:
                    f.write(f"  → {step['method']}({json.dumps(step['params'])})\n")
                    if step["error"]:
                        f.write(f"    error: {step['error']}\n")
                    else:
                        f.write(f"    result: {json.dumps(step['result'])[:200]}\n")
                f.write("\n")
        print(f"  Results written to: {results_path}")
    except Exception as e:
        print(f"  (Could not write results file: {e})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fuzz the Balatro API to find correct consumable targeting method.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="BalatroBot server host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="BalatroBot server port")
    parser.add_argument(
        "--consumable", type=int, default=0,
        help="0-based index of the consumable to use (default: 0 = first consumable)",
    )
    parser.add_argument(
        "--targets", type=int, nargs="+", default=[0, 1],
        help="0-based hand card indices to target (default: 0 1)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print full request/response for ALL probes, not just passing ones",
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Read-only mode: dump raw consumable and hand card JSON to discover field formats",
    )
    args = parser.parse_args()

    if args.inspect:
        run_inspect(host=args.host, port=args.port)
    else:
        run_fuzzer(
            host=args.host,
            port=args.port,
            consumable_idx=args.consumable,
            target_indices=args.targets,
            verbose=args.verbose,
        )
