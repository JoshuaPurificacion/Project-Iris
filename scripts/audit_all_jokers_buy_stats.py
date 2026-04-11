import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow importing project modules when run from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.balatro.shop_analysis import (
    _early_game_buy_block_reason,
    _extract_advisor_core,
    _summarize_shop_item,
)


def _load_openrpc_joker_descriptions(openrpc_path: Path) -> dict[str, str]:
    data = json.loads(openrpc_path.read_text(encoding="utf-8"))
    joker_schema = data["components"]["schemas"]["JokerKey"]
    descriptions: dict[str, str] = {}
    for entry in joker_schema.get("oneOf", []):
        key = entry.get("const")
        desc = entry.get("description") or ""
        if isinstance(key, str) and key.startswith("j_"):
            descriptions[key] = str(desc)
    return descriptions


def _load_joker_costs_and_names(game_lua_path: Path) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    key_re = re.compile(r"^\s*(j_[a-z0-9_]+)\s*=")
    name_re = re.compile(r"name\s*=\s*[\"']([^\"']+)[\"']")
    cost_re = re.compile(r"cost\s*=\s*(\d+)")

    for line in game_lua_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "set = \"Joker\"" not in line and "set = 'Joker'" not in line:
            continue
        key_match = key_re.search(line)
        if not key_match:
            continue
        key = key_match.group(1)

        name_match = name_re.search(line)
        cost_match = cost_re.search(line)

        catalog[key] = {
            "name": name_match.group(1) if name_match else key,
            "cost": int(cost_match.group(1)) if cost_match else 5,
        }

    return catalog


def _titleize_joker_key(key: str) -> str:
    return key[2:].replace("_", " ").title()


@dataclass
class Scenario:
    name: str
    ante: int
    money: int
    current_jokers: list[dict[str, Any]]


def _base_hand() -> list[dict[str, Any]]:
    # Flush-friendly hand baseline used by the bot's Checkered strategy.
    cards = [
        ("A", "Spades"),
        ("K", "Spades"),
        ("Q", "Spades"),
        ("J", "Spades"),
        ("9", "Spades"),
    ]
    return [
        {
            "key": f"{suit[0]}_{rank}",
            "label": "Base Card",
            "value": {"rank": rank, "suit": suit, "effect": ""},
        }
        for rank, suit in cards
    ]


def _scenario_state(s: Scenario, shop_card: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": "shop",
        "ante_num": s.ante,
        "money": s.money,
        "round": {"reroll_cost": 5, "chips": 0, "hands_left": 4, "discards_left": 3},
        "hand": {"cards": _base_hand()},
        "jokers": {"size": 5, "cards": list(s.current_jokers)},
        "shop": {"cards": [shop_card]},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "consumeables": {"size": 2, "cards": []},
    }


def _make_scenarios(joker_defs: dict[str, dict[str, Any]]) -> list[Scenario]:
    def card(key: str) -> dict[str, Any]:
        meta = joker_defs.get(key, {})
        return {
            "key": key,
            "label": str(meta.get("name") or _titleize_joker_key(key)),
            "value": {"effect": str(meta.get("effect") or "")},
        }

    return [
        Scenario(
            name="early_no_core",
            ante=1,
            money=10,
            current_jokers=[],
        ),
        Scenario(
            name="early_with_core",
            ante=2,
            money=25,
            current_jokers=[card("j_supernova"), card("j_scary_face")],
        ),
        Scenario(
            name="mid_flush_core",
            ante=5,
            money=40,
            current_jokers=[card("j_supernova"), card("j_scary_face"), card("j_blueprint")],
        ),
    ]


def _decision_from_summary(summary: dict[str, Any], raw_state: dict[str, Any], shop_card: dict[str, Any]) -> tuple[bool, str]:
    advisor_core = _extract_advisor_core(str(summary.get("advisor") or ""))

    if advisor_core == "BAD SYNERGY":
        return False, "bad_synergy"

    cost = shop_card.get("cost")
    try:
        cost_value = int(cost) if cost is not None else 0
    except (TypeError, ValueError):
        cost_value = 0

    money = int(raw_state.get("money", 0) or 0)
    if cost_value > money:
        return False, "insufficient_funds"

    early_block = _early_game_buy_block_reason(
        raw_state,
        shop_card,
        deck_name="CHECKERED",
        shop_summary=summary,
    )
    if early_block:
        return False, "early_reserve_block"

    if advisor_core in {"HIGH SYNERGY", "MODERATE VALUE"}:
        return True, "buy_signal"

    if advisor_core == "LOW VALUE":
        return False, "low_value"

    return False, "situational"


def main() -> None:
    appdata = Path(os.environ.get("APPDATA", ""))
    openrpc_path = appdata / "Balatro" / "Mods" / "balatrobot" / "src" / "lua" / "utils" / "openrpc.json"
    game_lua_path = appdata / "Balatro" / "Mods" / "lovely" / "game-dump" / "game.lua"

    if not openrpc_path.exists():
        raise SystemExit(f"Missing OpenRPC schema: {openrpc_path}")
    if not game_lua_path.exists():
        raise SystemExit(f"Missing game dump: {game_lua_path}")

    descs = _load_openrpc_joker_descriptions(openrpc_path)
    meta = _load_joker_costs_and_names(game_lua_path)

    joker_defs: dict[str, dict[str, Any]] = {}
    for key, desc in descs.items():
        row = meta.get(key, {})
        joker_defs[key] = {
            "key": key,
            "name": str(row.get("name") or _titleize_joker_key(key)),
            "cost": int(row.get("cost", 5)),
            "effect": desc,
        }

    scenarios = _make_scenarios(joker_defs)
    detailed_rows: list[dict[str, Any]] = []

    for key, j in sorted(joker_defs.items()):
        shop_card = {
            "key": key,
            "label": j["name"],
            "cost": j["cost"],
            "value": {"effect": j["effect"]},
        }

        for s in scenarios:
            raw_state = _scenario_state(s, shop_card)
            summary = _summarize_shop_item(shop_card, raw_state, "CHECKERED")
            would_buy, reason = _decision_from_summary(summary, raw_state, shop_card)
            detailed_rows.append(
                {
                    "joker_key": key,
                    "joker_name": j["name"],
                    "scenario": s.name,
                    "cost": j["cost"],
                    "advisor": str(summary.get("advisor") or ""),
                    "impact_pct": float(summary.get("impact_pct") or 0.0),
                    "would_buy": int(would_buy),
                    "reason": reason,
                }
            )

    total = len(detailed_rows)
    buys = sum(r["would_buy"] for r in detailed_rows)
    buy_rate = (buys / total * 100.0) if total else 0.0

    reason_counts: dict[str, int] = {}
    advisor_counts: dict[str, int] = {}
    per_joker_buy_rate: dict[str, dict[str, Any]] = {}

    for row in detailed_rows:
        reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + 1
        advisor_counts[row["advisor"]] = advisor_counts.get(row["advisor"], 0) + 1

        key = row["joker_key"]
        item = per_joker_buy_rate.setdefault(
            key,
            {"joker_name": row["joker_name"], "buy": 0, "n": 0},
        )
        item["buy"] += int(row["would_buy"])
        item["n"] += 1

    ranked = sorted(
        (
            {
                "joker_key": k,
                "joker_name": v["joker_name"],
                "buy_rate_pct": round((v["buy"] / v["n"] * 100.0) if v["n"] else 0.0, 2),
                "buy_count": v["buy"],
                "evaluations": v["n"],
            }
            for k, v in per_joker_buy_rate.items()
        ),
        key=lambda x: (-x["buy_rate_pct"], x["joker_key"]),
    )

    out_dir = Path(__file__).parent
    csv_path = out_dir / "joker_buy_audit.csv"
    json_path = out_dir / "joker_buy_audit_summary.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "joker_key",
                "joker_name",
                "scenario",
                "cost",
                "advisor",
                "impact_pct",
                "would_buy",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(detailed_rows)

    summary = {
        "catalog_size": len(joker_defs),
        "scenario_count": len(scenarios),
        "total_evaluations": total,
        "buy_count": buys,
        "buy_rate_pct": round(buy_rate, 2),
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "advisor_counts": dict(sorted(advisor_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "top_20_buy_rate": ranked[:20],
        "bottom_20_buy_rate": sorted(ranked, key=lambda x: (x["buy_rate_pct"], x["joker_key"]))[:20],
        "csv_path": str(csv_path),
    }

    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Joker buy audit complete")
    print(f"catalog_size={summary['catalog_size']}")
    print(f"scenario_count={summary['scenario_count']}")
    print(f"total_evaluations={summary['total_evaluations']}")
    print(f"buy_count={summary['buy_count']}")
    print(f"buy_rate_pct={summary['buy_rate_pct']}")
    print(f"csv={csv_path}")
    print(f"summary={json_path}")


if __name__ == "__main__":
    main()
