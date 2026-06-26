"""
Shop analysis helpers: economy thresholds, item summarization, and strategy text.

Pure helpers with no session state. Imported by session.py, actions/shop.py, and
prompt_builder.py to avoid duplicating shop logic across the codebase.

No imports from session.py - this module sits below session in the dependency graph.
"""
from __future__ import annotations

from typing import Any

from skills.balatro_bot.modules.algorithms import BalatroAlgorithm
from skills.balatro.utils import _get_state_cards, _safe_int

# ---------------------------------------------------------------------------
# Economy thresholds
# ---------------------------------------------------------------------------

EARLY_GAME_INTEREST_RESERVE = 25  # Reserve to enforce once the joker target is reached
EARLY_GAME_ANTE_LIMIT = 2  # Guardrail only active for Antes 1-2
EARLY_GAME_JOKER_TARGET = 3  # Allow buying up to 3 scoring Jokers before enforcing reserve
EARLY_GAME_LOW_RESERVE = 10  # Reserve while below the joker target
MID_GAME_RESERVE = 10  # Reserve for Antes 3+ in shop strategy block
CONSUMABLE_EXCEPTION_FLOOR = 20  # Can dip this low for highly impactful Tarot/Planet buys
CRITICAL_SPEND_FLOOR = 15  # Absolute emergency floor for critical upgrades


# ---------------------------------------------------------------------------
# Phase-aware reserve helper
# ---------------------------------------------------------------------------

def _get_reserve_for_ante(ante: int, money: int) -> int:  # noqa: ARG001
    """Return the interest reserve target appropriate for the current ante.

    Early game (Antes 1-2): keep $25 — interest income is meaningful.
    Mid game  (Antes 3-4): relax to $10 — survival buys take priority.
    Late game (Ante  5+) : hold only $5  — spend aggressively, interest
                           is negligible vs. the value of a good xMult joker.
    The ``money`` parameter is accepted for future scaling extensions but is
    not used at this stage.
    """
    if ante <= 2:
        return EARLY_GAME_INTEREST_RESERVE  # $25
    if ante <= 4:
        return MID_GAME_RESERVE             # $10
    return 5                               # Late game: nearly no reserve


# ---------------------------------------------------------------------------
# Level-map resolution
# ---------------------------------------------------------------------------

def _resolve_explicit_level_maps(
    raw_state: dict,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Expose explicit hand/planet level metadata from API state if present."""
    hand_levels = raw_state.get("hand_levels")
    planet_levels = raw_state.get("planet_levels")

    hand_levels_map = hand_levels if isinstance(hand_levels, dict) else {}
    planet_levels_map = planet_levels if isinstance(planet_levels, dict) else {}

    diagnostic = (
        "explicit-levels-present"
        if hand_levels_map or planet_levels_map
        else "explicit-levels-missing"
    )
    return hand_levels_map, planet_levels_map, diagnostic


def _get_hand_stats(raw_state: dict) -> dict[str, int]:
    """Collect lightweight hand stats used by shop heuristics."""
    current_jokers = _get_state_cards(raw_state, "jokers")
    hand_cards = _get_state_cards(raw_state, "hand")
    round_info = raw_state.get("round") or {}
    total_chips = round_info.get("chips", 0) if isinstance(round_info, dict) else 0
    return {
        "total_chips": _safe_int(total_chips, 0),
        "joker_count": len(current_jokers),
        "hand_count": len(hand_cards),
    }


# ---------------------------------------------------------------------------
# Card-type detectors
# ---------------------------------------------------------------------------

def _extract_cost_value(cost: Any) -> int | None:
    """Extract numeric cost from dict or int format."""
    if cost is None:
        return None
    if isinstance(cost, int):
        return cost
    if isinstance(cost, dict):
        return cost.get("buy") or cost.get("sell")
    return None


def _extract_advisor_core(advisor: str) -> str:
    """Return base advisor label without inline bracket tags."""
    clean = str(advisor or "").strip()
    if not clean:
        return ""
    return clean.split("[", 1)[0].strip().upper()


def _is_buffoon_pack(card: dict) -> bool:
    """Best-effort Buffoon pack detector."""
    key = str(card.get("key") or "").lower()
    label = str(card.get("label") or "").lower()
    effect = str((card.get("value") or {}).get("effect") or "").lower()
    return "buffoon" in key or "buffoon" in label or "joker" in effect


def _is_planet_card(card: dict) -> bool:
    """Best-effort Planet card detector."""
    key = str(card.get("key") or "").lower()
    label = str(card.get("label") or "").lower()
    effect = str((card.get("value") or {}).get("effect") or "").lower()
    planet_names = (
        "pluto",
        "mercury",
        "uranus",
        "venus",
        "saturn",
        "jupiter",
        "earth",
        "mars",
        "neptune",
        "planet x",
        "ceres",
        "eris",
    )
    if key.startswith("c_planet") or "planet" in key or "planet" in label:
        return True
    return any(name in label or name in effect for name in planet_names)


def _is_tarot_card(card: dict) -> bool:
    """Best-effort Tarot card detector."""
    key = str(card.get("key") or "").lower()
    label = str(card.get("label") or "").lower()
    tarot_names = {
        "fool",
        "magician",
        "high priestess",
        "empress",
        "emperor",
        "hierophant",
        "lovers",
        "chariot",
        "justice",
        "hermit",
        "wheel of fortune",
        "strength",
        "hanged man",
        "death",
        "temperance",
        "devil",
        "tower",
        "star",
        "moon",
        "sun",
        "judgement",
        "world",
    }
    if key.startswith("c_tarot"):
        return True
    return label in tarot_names


def _has_tarot_usage_scaling_joker(jokers: list[dict]) -> bool:
    """True when the lineup contains a joker that scales from Tarot usage."""
    for joker in jokers:
        if not isinstance(joker, dict):
            continue
        effect = str((joker.get("value") or {}).get("effect") or "").lower()
        if (
            "tarot" in effect
            and "per tarot" in effect
            and (
                "used this run" in effect
                or "this run" in effect
                or "tarot cards used" in effect
            )
        ):
            return True
    return False


def _build_economy_tag(
    card: dict,
    summary: dict[str, str | int],
    raw_state: dict,
    deck_name: str = "",
) -> str:
    """Compute economy status tags for shop rendering without mutating item names."""
    cost = _extract_cost_value(summary.get("cost"))
    if cost is None or cost <= 0:
        return ""

    money = _safe_int(raw_state.get("money"), 0)
    ante = _safe_int(raw_state.get("ante_num"), 1)
    phase_reserve = _get_reserve_for_ante(ante, money)
    post_buy_money = money - cost
    if post_buy_money >= phase_reserve:
        return "[SAFE TO BUY]"

    advisor = _extract_advisor_core(str(summary.get("advisor") or ""))
    card_key = str(card.get("key") or "").lower()

    critical_upgrade = False
    if card_key.startswith("v_"):
        critical_upgrade = True
    elif _is_buffoon_pack(card) and ante <= EARLY_GAME_ANTE_LIMIT:
        critical_upgrade = True
    elif advisor == "HIGH SYNERGY":
        critical_upgrade = True

    impactful_consumable = _is_planet_card(card) or _is_tarot_card(card)
    if impactful_consumable and post_buy_money >= CONSUMABLE_EXCEPTION_FLOOR:
        return "[CRITICAL UPGRADE]"

    if critical_upgrade and post_buy_money >= CRITICAL_SPEND_FLOOR:
        return "[CRITICAL UPGRADE]"

    return "[COSTS INTEREST]"


def _summarize_shop_item(
    card: dict | None, raw_state: dict, deck_name: str = ""
) -> dict[str, Any]:
    """Return a human-readable summary for logging and persona reactions."""
    if not isinstance(card, dict):
        return {
            "label": "Unknown item",
            "cost": 0,
            "effect": "",
            "advisor": "",
            "economy_tag": "",
            "impact_pct": 0.0,
            "baseline_score": 0,
            "projected_score": 0,
            "expected_score": 0,
            "immediate_impact": 0,
            "projected_3_round_impact": 0,
            "replace_idx": "",
            "replacement_index": None,
        }

    summary: dict[str, Any] = {
        "label": str(card.get("label") or card.get("key") or "Unknown item"),
        "cost": card.get("cost"),
        "effect": str((card.get("value") or {}).get("effect") or ""),
        "advisor": "",
        "economy_tag": "",
        "impact_pct": 0.0,
        "baseline_score": 0,
        "projected_score": 0,
        "expected_score": 0,
        "immediate_impact": 0,
        "projected_3_round_impact": 0,
        "replace_idx": "",
        "replacement_index": None,
    }

    current_jokers = _get_state_cards(raw_state, "jokers")
    hand_cards = _get_state_cards(raw_state, "hand")
    hand_levels_map, planet_levels_map, _ = _resolve_explicit_level_maps(raw_state)

    if BalatroAlgorithm.is_probable_joker(card):
        try:
            impact = BalatroAlgorithm.evaluate_shop_joker_impact(
                card,
                current_jokers,
                hand_cards,
                deck_name=deck_name,
                deck_state=raw_state,
                hand_levels=hand_levels_map,
                planet_levels=planet_levels_map,
            )
            summary["impact_pct"] = float(impact.get("delta_pct", 0.0))
            summary["baseline_score"] = int(impact.get("baseline_score", 0) or 0)
            summary["projected_score"] = int(impact.get("projected_score", 0) or 0)
            summary["expected_score"] = int(impact.get("expected_score", 0) or 0)
            summary["immediate_impact"] = int(
                impact.get("immediate_impact", impact.get("delta", 0)) or 0
            )
            summary["projected_3_round_impact"] = int(
                impact.get("projected_3_round_impact", impact.get("expected_delta", 0))
                or 0
            )
            replace_idx = impact.get("replacement_index")
            if replace_idx is not None:
                summary["replace_idx"] = str(replace_idx)
                summary["replacement_index"] = replace_idx

            summary["advisor"] = BalatroAlgorithm.score_joker_value(
                card,
                current_jokers,
                _get_hand_stats(raw_state),
                deck_name,
                hand_cards=hand_cards,
                deck_state=raw_state,
            )
        except Exception:
            summary["advisor"] = ""
            summary["impact_pct"] = 0.0
    elif str(card.get("key") or "").lower().startswith("v_"):
        try:
            summary["advisor"] = BalatroAlgorithm.score_voucher_value(
                card,
                _safe_int(raw_state.get("money"), 0),
                _safe_int(raw_state.get("ante_num"), 1),
            )
        except Exception:
            summary["advisor"] = ""
    elif _is_tarot_card(card) and _has_tarot_usage_scaling_joker(current_jokers):
        summary["advisor"] = "HIGH SYNERGY"
        # Encourage immediate Tarot pickup when a tarot-scaling joker is active.
        summary["impact_pct"] = 18.0

    summary["economy_tag"] = _build_economy_tag(
        card,
        summary,
        raw_state,
        deck_name=deck_name,
    )

    return summary


def _format_shop_item_summary(summary: dict[str, str | int]) -> str:
    """Format a concise item description for terminal logs."""
    label = str(summary.get("label") or "Unknown item")
    cost = summary.get("cost")
    advisor = str(summary.get("advisor") or "").strip()
    economy_tag = str(summary.get("economy_tag") or "").strip()
    impact_pct = summary.get("impact_pct", 0.0)
    immediate_impact = summary.get("immediate_impact", 0)
    projected_impact = summary.get("projected_3_round_impact", 0)
    replace_idx = str(summary.get("replace_idx") or "").strip()

    if cost is None:
        cost_str = "cost=unknown"
    elif isinstance(cost, dict):
        cost_str = f"cost=${cost.get('buy', cost.get('sell', '?'))}"
    else:
        cost_str = f"cost=${cost}"

    detail_parts = [cost_str]
    if advisor:
        detail_parts.append(f"advisor={advisor}")
    if economy_tag:
        detail_parts.append(f"economy={economy_tag}")

    try:
        impact_value = float(impact_pct)
    except (TypeError, ValueError):
        impact_value = 0.0
    if abs(impact_value) > 0.0:
        detail_parts.append(f"impact={impact_value:+.1f}%")

    try:
        immediate_val = int(immediate_impact)
    except (TypeError, ValueError):
        immediate_val = 0
    try:
        projected_val = int(projected_impact)
    except (TypeError, ValueError):
        projected_val = 0

    if immediate_val != 0:
        detail_parts.append(f"immediate={immediate_val:+d}")
    if projected_val != 0:
        detail_parts.append(f"projected3r={projected_val:+d}")
    if replace_idx:
        detail_parts.append(f"replace=#{replace_idx}")

    return f"{label} ({', '.join(detail_parts)})"


def format_cost_for_display(cost: Any) -> str:
    """Format cost value for persona messages (e.g., '5' or '?')."""
    numeric_cost = _extract_cost_value(cost)
    return str(numeric_cost) if numeric_cost is not None else "?"


def _any_affordable(raw_state: dict, money: int) -> bool:
    """Return True if at least one shop/pack/voucher item can be bought with money."""
    for container in ("shop", "packs", "vouchers"):
        for card in _get_state_cards(raw_state, container):
            cost = _extract_cost_value(card.get("cost"))
            if cost is not None and cost > 0 and money >= cost:
                return True
    return False


def _get_shop_card_by_index(raw_state: dict, card_idx: int) -> dict | None:
    """Resolve a 1-based shop-card index into the matching raw card dict."""
    shop_cards = _get_state_cards(raw_state, "shop")
    zero_idx = card_idx - 1
    if 0 <= zero_idx < len(shop_cards):
        return shop_cards[zero_idx]
    return None


def _has_survival_joker(raw_state: dict) -> bool:
    """True when the owned Joker lineup already has direct Chips/Mult scaling."""
    return any(
        BalatroAlgorithm.joker_has_scoring_effect(joker)
        for joker in _get_state_cards(raw_state, "jokers")
    )


def _count_survival_jokers(raw_state: dict) -> int:
    """Count how many owned Jokers have direct Chips/Mult/xMult scaling."""
    return sum(
        1
        for joker in _get_state_cards(raw_state, "jokers")
        if BalatroAlgorithm.joker_has_scoring_effect(joker)
    )


def _early_game_buy_block_reason(
    raw_state: dict,
    shop_card: dict,
    deck_name: str = "",
    shop_summary: dict[str, Any] | None = None,
) -> str | None:
    """Block risky early-game buys until a base of scoring Jokers has been secured.

    Tiered logic:
    - 0 scoring Jokers: only block non-Joker buys that drop below LOW_RESERVE ($10).
    - 1-2 scoring Jokers (below JOKER_TARGET=3): enforce LOW_RESERVE, always allow
      buying another scoring Joker.
    - 3+ scoring Jokers: enforce full INTEREST_RESERVE ($25).
    - HIGH SYNERGY / impact_pct>=20 items bypass the reserve with critical floor.
    - Guardrail is off entirely for Antes 3+ (ante > EARLY_GAME_ANTE_LIMIT).
    """
    ante = _safe_int(raw_state.get("ante_num"), 1)
    if ante > EARLY_GAME_ANTE_LIMIT:
        return None

    money = _safe_int(raw_state.get("money"), 0)
    cost = _extract_cost_value(shop_card.get("cost"))
    if cost is None or cost <= 0:
        return None

    survival_count = _count_survival_jokers(raw_state)
    effective_reserve = (
        EARLY_GAME_INTEREST_RESERVE
        if survival_count >= EARLY_GAME_JOKER_TARGET
        else EARLY_GAME_LOW_RESERVE
    )

    post_buy_money = money - cost
    if post_buy_money >= effective_reserve:
        return None

    # Reuse precomputed summary when available to avoid duplicate impact evaluation.
    summary = (
        dict(shop_summary)
        if isinstance(shop_summary, dict)
        else _summarize_shop_item(shop_card, raw_state, deck_name)
    )
    impact_pct = float(summary.get("impact_pct") or 0.0)
    advisor_core = _extract_advisor_core(str(summary.get("advisor") or ""))

    if (
        _is_planet_card(shop_card) or _is_tarot_card(shop_card)
    ) and post_buy_money >= CONSUMABLE_EXCEPTION_FLOOR:
        return None

    if (
        advisor_core == "HIGH SYNERGY" or impact_pct >= 20.0
    ) and post_buy_money >= CRITICAL_SPEND_FLOOR:
        return None

    shop_card_is_survival = BalatroAlgorithm.is_probable_joker(
        shop_card
    ) and BalatroAlgorithm.joker_has_scoring_effect(shop_card)
    label = str(shop_card.get("label") or shop_card.get("key") or "Unknown item")

    if survival_count == 0 and money < effective_reserve:
        return None

    if survival_count < EARLY_GAME_JOKER_TARGET:
        if shop_card_is_survival:
            return None
        return (
            f"Early-game reserve: {survival_count}/{EARLY_GAME_JOKER_TARGET} scoring Jokers. "
            f"Keep ${effective_reserve} for Joker buys. "
            f"{label} costs ${cost}, money=${money}. Buy a scoring Joker first."
        )

    return (
        f"Early-game reserve: {survival_count}/{EARLY_GAME_JOKER_TARGET} scoring Jokers "
        f"(target met). Keep ${EARLY_GAME_INTEREST_RESERVE} banked. "
        f"{label} costs ${cost}, money=${money}; "
        f"only spend surplus above ${EARLY_GAME_INTEREST_RESERVE}."
    )


def _build_shop_strategy_block(
    raw_state: dict,
    hand_cards: list[dict],
    deck_name: str = "",
    debuffed_ranks: list[str] | None = None,
    debuffed_suits: list[str] | None = None,
    allows_four_card_hands: bool = False,
    allows_gaps: bool = False,
    shop_cache: Any | None = None,
) -> str:
    """Build a compact strategy block for shop turns with joker replacement math."""
    state_name = str(raw_state.get("state") or "").lower()
    if "shop" not in state_name:
        return ""

    money = _safe_int(raw_state.get("money"), 0)
    ante = _safe_int(raw_state.get("ante_num"), 1)
    survival_count_strat = _count_survival_jokers(raw_state)
    reserve = _get_reserve_for_ante(ante, money)
    surplus = max(0, money - reserve)
    reroll_cost = _safe_int((raw_state.get("round") or {}).get("reroll_cost"), 0)

    current_jokers = (
        shop_cache.jokers if shop_cache is not None else _get_state_cards(raw_state, "jokers")
    )
    joker_slots_total = _safe_int((raw_state.get("jokers") or {}).get("size"), 5)
    shop_cards = (
        shop_cache.shop_cards if shop_cache is not None else _get_state_cards(raw_state, "shop")
    )
    vouchers = (
        shop_cache.voucher_cards if shop_cache is not None else _get_state_cards(raw_state, "vouchers")
    )
    packs = (
        shop_cache.pack_cards if shop_cache is not None else _get_state_cards(raw_state, "packs")
    )

    shop_summaries = (
        list(shop_cache.shop_summaries)
        if shop_cache is not None
        else [_summarize_shop_item(card, raw_state, deck_name) for card in shop_cards]
    )
    pack_summaries = (
        list(shop_cache.pack_summaries)
        if shop_cache is not None
        else [_summarize_shop_item(card, raw_state, deck_name) for card in packs]
    )
    voucher_summaries = (
        list(shop_cache.voucher_summaries)
        if shop_cache is not None
        else [_summarize_shop_item(card, raw_state, deck_name) for card in vouchers]
    )

    safe_sells = BalatroAlgorithm.identify_safe_sell_jokers(
        current_jokers,
        hand_cards,
        deck_name=deck_name,
        tolerance_pct=2.5,
        debuffed_ranks=debuffed_ranks,
        debuffed_suits=debuffed_suits,
        allows_four_card_hands=allows_four_card_hands,
        allows_gaps=allows_gaps,
        current_money=money,
        max_jokers=joker_slots_total,
    )
    safe_by_index = {int(row.get("index", 0)): row for row in safe_sells}

    if shop_cache is not None:
        hand_levels_map = shop_cache.hand_levels_map
        planet_levels_map = shop_cache.planet_levels_map
        level_diag = (
            "explicit-levels-present"
            if hand_levels_map or planet_levels_map
            else "explicit-levels-missing"
        )
    else:
        hand_levels_map, planet_levels_map, level_diag = _resolve_explicit_level_maps(
            raw_state
        )

    baseline = BalatroAlgorithm.estimate_best_score(
        hand_cards,
        current_jokers=current_jokers,
        deck_name=deck_name,
        debuffed_ranks=debuffed_ranks,
        debuffed_suits=debuffed_suits,
        allows_four_card_hands=allows_four_card_hands,
        allows_gaps=allows_gaps,
        hand_levels=hand_levels_map,
        planet_levels=planet_levels_map,
    )

    next_blind_score: int | None = None
    blinds_data = raw_state.get("blinds") or {}
    if isinstance(blinds_data, dict):
        blind_priority = {"upcoming": 0, "next": 1, "active": 2, "current": 3}
        best_priority = 999
        for _blind_key, blind_value in blinds_data.items():
            if not isinstance(blind_value, dict):
                continue
            status = str(blind_value.get("status", "")).lower()
            if status in blind_priority and blind_priority[status] < best_priority:
                best_priority = blind_priority[status]
                next_blind_score = blind_value.get("score") or blind_value.get(
                    "chips_required"
                )

    joker_slots_used = len(current_jokers)
    joker_slots_free = max(0, joker_slots_total - joker_slots_used)

    has_bad_synergy_shop_item = False
    all_bad_or_low_shop = bool(shop_summaries)
    for summary in shop_summaries:
        advisor_core = _extract_advisor_core(str(summary.get("advisor") or ""))
        if advisor_core == "BAD SYNERGY":
            has_bad_synergy_shop_item = True
        if advisor_core not in ("BAD SYNERGY", "LOW VALUE", "SITUATIONAL", ""):
            all_bad_or_low_shop = False

    has_early_buffoon_pack = ante <= EARLY_GAME_ANTE_LIMIT and any(
        _is_buffoon_pack(card) for card in packs
    )

    lines: list[str] = [
        "*** SHOP STRATEGY ***",
        f"- Money=${money}; reserve_target=${reserve}; surplus_above_${reserve}=${surplus}; reroll_cost=${reroll_cost}",
        f"- Joker slots: {joker_slots_used}/{joker_slots_total} ({joker_slots_free} FREE) | "
        f"Scoring Jokers: {survival_count_strat}/{EARLY_GAME_JOKER_TARGET} target",
        f"- Theoretical best hand score with current jokers: {baseline}",
        f"- Level modeling: {level_diag}",
        f"- Rule hierarchy: (1) Survive next Blind. (2) Maintain ${reserve}+ reserve when possible.",
        f"- Spending exceptions: down to $20 for impactful Tarot/Planet, down to ${CRITICAL_SPEND_FLOOR} for critical survival upgrades.",
    ]
    if next_blind_score:
        lines.append(
            f"- NEXT BLIND TARGET: {next_blind_score} chips - evaluate if current Jokers can hit this score"
        )

    if current_jokers:
        lines.append("- Current jokers (sell policy):")
        for idx, joker in enumerate(current_jokers, start=1):
            name = str(joker.get("label") or joker.get("key") or f"Joker {idx}")
            safe_info = safe_by_index.get(idx)
            if safe_info:
                loss = float(safe_info.get("loss_pct", 0.0))
                reason = str(safe_info.get("reason") or "LOW IMPACT")
                lines.append(
                    f"  [{idx}] {name} [SAFE TO SELL] ({reason}, {loss:.1f}% score loss)"
                )
            else:
                lines.append(f"  [{idx}] {name} -> KEEP")
    else:
        lines.append("- Current jokers: none")

    if shop_cards:
        lines.append("- Shop cards:")
        for idx, (card, summary) in enumerate(zip(shop_cards, shop_summaries), start=1):
            item_line = f"  [{idx}] {_format_shop_item_summary(summary)}"
            advisor_core = _extract_advisor_core(str(summary.get("advisor") or ""))
            if BalatroAlgorithm.is_probable_joker(card):
                replace_idx = summary.get("replace_idx")
                impact_pct = float(summary.get("impact_pct") or 0.0)
                if advisor_core == "BAD SYNERGY":
                    item_line += " -> AVOID: BAD SYNERGY"
                if replace_idx:
                    try:
                        replace_idx_int = int(str(replace_idx))
                        improvement = float(summary.get("impact_pct") or 0.0)
                        if replace_idx_int in safe_by_index and improvement >= 8.0:
                            item_line += (
                                f" -> RECOMMENDED: sell_joker [{replace_idx_int}] "
                                f"then buy_shop [{idx}]"
                            )
                        elif replace_idx_int in safe_by_index and improvement < 8.0:
                            item_line += (
                                f" -> HOLD: {improvement:.1f}% improvement does not justify selling"
                            )
                        else:
                            item_line += (
                                f" -> slot full: #{replace_idx_int} is NOT safe to sell"
                            )
                    except (TypeError, ValueError):
                        pass
                if impact_pct >= 15.0 and not replace_idx:
                    item_line += " -> RECOMMENDED: high-impact buy if affordable"
            lines.append(item_line)
    else:
        lines.append("- Shop cards: empty")

    if voucher_summaries:
        voucher_summary = voucher_summaries[0]
        lines.append(f"- Voucher: {_format_shop_item_summary(voucher_summary)}")

    if pack_summaries:
        lines.append("- Packs available:")
        for idx, summary in enumerate(pack_summaries, start=1):
            lines.append(f"  [{idx}] {_format_shop_item_summary(summary)}")

    if has_bad_synergy_shop_item:
        lines.append(
            "- Safety override active: BAD SYNERGY items are blocked. Choose a different action."
        )

    if has_early_buffoon_pack and survival_count_strat == 0:
        lines.append(
            "- Early-game urgency: no scoring Joker yet and Buffoon pack exists. Prioritize buy_pack for Buffoon first."
        )

    if surplus > 0:
        lines.append(
            "- Spend surplus intentionally: voucher -> Buffoon (early/weak) -> Tarot/Planet packs -> high-synergy jokers."
        )
    else:
        lines.append(
            "- No true surplus: avoid low-impact spending unless it improves immediate blind survival."
        )

    if reroll_cost > 0:
        reroll_post_money = money - reroll_cost
        if all_bad_or_low_shop and reroll_post_money >= reserve:
            lines.append(
                f"- Reroll note: ALL shop items are low/bad and reroll keeps >=${reserve}. Reroll is valid."
            )
        elif all_bad_or_low_shop and reroll_post_money >= CRITICAL_SPEND_FLOOR:
            lines.append(
                "- Reroll note: low-quality shop. Reroll may dip interest but is allowed for survival down to $15."
            )
        else:
            lines.append(
                "- Reroll note: reroll only when shop is weak and post-reroll economy floor rules are respected."
            )

    if safe_sells:
        sell_indices = [
            str(int(row.get("index", 0)))
            for row in safe_sells[:2]
            if int(row.get("index", 0)) > 0
        ]
        if sell_indices:
            lines.append(
                f"- [SAFE TO SELL] candidates (ONLY valid when all slots are full): {', '.join(sell_indices)}"
            )

    lines.append("*** END SHOP STRATEGY ***")
    return "\n".join(lines)

