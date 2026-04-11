# Project Iris — Ante 8 Fix Plan

Six concrete problems in the codebase. Here they are in the order you should fix them, with the exact location of each bug and what to change.

## ✅ Fix 1 — The Reroll Infinite Loop (Critical, fix first)
**File:** skills/balatro/actions/shop.py — RerollAction.execute()
**What's actually happening:** The bot correctly identifies it wants to reroll (good shop logic, hoarded $60+). But _find_affordable_non_rerollable_option() returns any affordable voucher or pack with a rating better than BAD SYNERGY. A LOW VALUE voucher qualifies. So the bot is forced to buy the bad voucher first. After buying it, the next shop cycle may have another affordable item, so reroll gets blocked again. The bot never actually gets to reroll — it just burns money on mediocre vouchers and packs in a slow loop.
**Fix:** In _find_affordable_non_rerollable_option(), raise the bar. Only block reroll for vouchers/packs rated HIGH SYNERGY or MODERATE VALUE. LOW VALUE and SITUATIONAL items must not gate reroll. Change:
`python
def _advisor_not_bad(summary: dict) -> bool:
    advisor = _extract_advisor_core(str(summary.get("advisor") or ""))
    return advisor != "BAD SYNERGY"
`
To:
`python
def _advisor_not_bad(summary: dict) -> bool:
    advisor = _extract_advisor_core(str(summary.get("advisor") or ""))
    return advisor in ("HIGH SYNERGY", "MODERATE VALUE")
`
This means reroll is only blocked when there's a genuinely good non-rerollable item visible. Low and situational items no longer gate it.

## ✅ Fix 2 — Economy Model Never Transitions Out of Early Game (Critical)
**File:** skills/balatro/shop_analysis.py — _build_shop_strategy_block()
**What's actually happening:** reserve = EARLY_GAME_INTEREST_RESERVE is hardcoded to $25 regardless of ante. The RerollAction also uses this same constant. At Ante 6+ the bot should be spending aggressively, not hoarding $25 for interest. Interest income becomes irrelevant at that point compared to the survival value of a good xMult joker.
**Fix:** Replace the flat reserve with a phase-aware function at the top of shop_analysis.py:
`python
def _get_reserve_for_ante(ante: int, money: int) -> int:
    if ante <= 2:
        return EARLY_GAME_INTEREST_RESERVE  # $25
    if ante <= 4:
        return MID_GAME_RESERVE             # $10
    return 5  # Late game: nearly no reserve, spend on survival
`
Then replace every bare reserve = EARLY_GAME_INTEREST_RESERVE inside _build_shop_strategy_block() and RerollAction.execute() with reserve = _get_reserve_for_ante(ante, money). Also update the surplus calculation accordingly.
This unlocks mid and late game spending. At Ante 5+ the bot can now reroll, buy xMult jokers, and spend down to $5 instead of being frozen at $25.

## Fix 3 — Joker Selling Without Confirmed Replacement Value (The sell bug you flagged)
**File:** skills/balatro/actions/shop.py — _build_dynamic_slot_full_error()
Also: skills/balatro/shop_analysis.py — identify_safe_sell_jokers()
**What's actually happening:** When slots are full, _build_dynamic_slot_full_error() calls identify_safe_sell_jokers() with tolerance_pct=100.0 — meaning it will always find a joker to suggest selling regardless of how good it is. The shop strategy block then says RECOMMENDED: sell_joker [X] then buy_shop [Y] without verifying the replacement is genuinely better than the joker being sold.
The LLM follows this recommendation. Good jokers get sold for mediocre ones because the slot-full path has no minimum improvement gate.
**Fix — two parts:**
**Part A:** In _build_dynamic_slot_full_error(), change the tolerance from 100.0 to something reasonable (5.0 max). The sell hint should only fire if the best sell candidate's loss is genuinely low:
`python
safe_sells = BalatroAlgorithm.identify_safe_sell_jokers(
    current_jokers=jokers,
    hand_cards=hand_cards,
    tolerance_pct=5.0,   # was 100.0
    deck_state=raw_state,
)
`
**Part B:** In _build_shop_strategy_block(), where it generates the RECOMMENDED: sell_joker [X] then buy_shop [Y] line, add a guard that the replacement's expected_score must exceed the current board's baseline_expected_score by at least a meaningful threshold (say 8%). If it doesn't, output HOLD: replacement does not justify selling instead. The specific check goes here in the strategy block loop:
`python
if replace_idx:
    replace_idx_int = int(str(replace_idx))
    improvement = float(summary.get("impact_pct") or 0.0)
    if replace_idx_int in safe_by_index and improvement >= 8.0:
        item_line += f" -> RECOMMENDED: sell_joker [{replace_idx_int}] then buy_shop [{idx}]"
    elif improvement < 8.0:
        item_line += f" -> HOLD: {improvement:.1f}% improvement does not justify selling"
    else:
        item_line += f" -> slot full: #{replace_idx_int} is NOT safe to sell"
`
This directly fixes the "sells jokers when not needed" problem. Selling now only happens when the math confirms the swap is worth it.

## Fix 4 — xMult Jokers Undervalued in Late Game (High)
**File:** skills/balatro_bot/modules/algorithms.py — _estimate_joker_immediate_additive_value()
**What's actually happening:** A x2 Mult joker scores (2.0 - 1.0) * 60.0 = 60. A +40 Mult joker scores 40 * 1.5 = 60. They're treated as equal. At Ante 5+ with a developed board, x2 Mult is worth ten times more because it multiplies the entire chip×mult product. The flat scoring model doesn't account for this.
**Fix:** Scale xMult value by the current baseline score so it reflects its true exponential impact:
`python
@staticmethod
def _estimate_joker_immediate_additive_value(
    joker: Dict[str, Any],
    baseline_score: float = 0.0,   # add this param
) -> float:
    effect = BalatroAlgorithm._card_text(joker)
    chips_hits = BalatroAlgorithm._extract_all_numbers(effect, r"\+(\d+(?:\.\d+)?)\s*chips?")
    mult_hits = BalatroAlgorithm._extract_all_numbers(effect, r"\+(\d+(?:\.\d+)?)\s*mult")
    xmult_hits = BalatroAlgorithm._extract_xmult_values(effect)
    chips_score = sum(chips_hits)
    mult_score = sum(mult_hits) * 1.5
    # If we know the baseline, score xMult as actual score delta it produces
    if baseline_score > 0 and xmult_hits:
        xmult_score = sum((hit - 1.0) * baseline_score for hit in xmult_hits)
    else:
        xmult_score = sum(max(0.0, hit - 1.0) * 60.0 for hit in xmult_hits)
    return chips_score + mult_score + xmult_score
`
Then update evaluate_shop_joker_impact() to pass baseline_score into this function when it calls it. The xMult joker will now correctly score as far superior to a flat +Mult joker at Ante 5+, and the sell/buy recommendations will favor building a multiplicative engine.
Also update score_joker_value(): at Ante 5+, flat +Mult jokers should be downgraded to SITUATIONAL rather than MODERATE VALUE unless the lineup has no xMult yet. Add this block:
`python
ante = int((deck_state or {}).get("ante_num", 1))
if ante >= 5 and ("+mult" in effect) and not BalatroAlgorithm._extract_xmult_values(effect):
    # Check if lineup already has xMult
    lineup_has_xmult = any(
        BalatroAlgorithm._extract_xmult_values(BalatroAlgorithm._card_text(j))
        for j in current_jokers
    )
    if lineup_has_xmult:
        return "LOW VALUE"
`

## Fix 5 — Discard Has No Committed Hand Type (High)
**File:** skills/balatro_bot/modules/algorithms.py — find_best_discard()
**What's actually happening:** Each discard decision is made fresh with no memory of what hand type the session is building toward. On Checkered deck the bot is committed to Flush, but find_best_discard() can still choose to protect a straight draw over flush cards if the straight draw happens to be in the hand at that moment. There's no session-level hand type commitment.
**Fix:** Add an optional target_hand_type parameter to find_best_discard(). When provided, it skips the generic flush/straight draw detection and goes straight to the committed strategy:
`python
@staticmethod
def find_best_discard(
    hand_cards: List[Dict[str, Any]],
    best_play_indices: List[int],
    target_hand_type: Optional[str] = None,  # "Flush", "Straight", "Pair", etc.
) -> List[int]:
`
Then in the Checkered deck path (which always uses Flush), call find_best_discard(..., target_hand_type="Flush"). When target_hand_type == "Flush", skip the straight draw protection logic entirely and always discard non-flush-suit cards. This makes flush draws consistent.
In session.py or wherever find_best_discard is called, derive the target hand type from estimate_best_score's top option at the start of each blind and pass it through. One pre-blind decision, then all discards that hand serve it.

## Fix 6 — Blind Score Targets Don't Scale Past Ante 4 (Medium)
**File:** skills/balatro/shop_analysis.py — _build_shop_strategy_block()
**What's actually happening:** The strategy block reads next_blind_score from the API correctly, but the survival gate in BuyShopAction compares immediate_expected against current_blind_requirement * boss_mult. The problem is estimate_best_score() doesn't account for Joker scaling during the actual play phase — it's a snapshot estimate. By Ante 5+ the blind requirements jump so hard (200k+) that the snapshot estimate always looks insufficient, potentially blocking good pivots.
**Fix:** Add a BLIND_SAFETY_MARGIN_BY_ANTE map that relaxes the gate threshold at higher antes, acknowledging that scoring estimates are imprecise at scale:
`python
BLIND_SAFETY_MARGIN_BY_ANTE = {
    1: 1.5,   # Tight early — estimates are reliable
    2: 1.5,
    3: 1.3,
    4: 1.2,
    5: 1.0,   # Trust the estimate, margin already baked into boss_mult
    6: 0.9,   # Allow pivots even if estimate is slightly under
    7: 0.85,
    8: 0.8,
}
`
In BuyShopAction, change the gate to:
`python
margin = BLIND_SAFETY_MARGIN_BY_ANTE.get(ante, 1.0)
gated_requirement = int(round(current_blind_requirement * boss_mult * margin))
`
This prevents the survival gate from over-blocking late-game pivots to better jokers just because the score estimator undershoots the real potential.

## Summary — Execution Order
Fix them in this exact order. Each one unblocks the next:

Fix 1 (Reroll loop) — Unblocks everything. Bot can now spend correctly.
Fix 2 (Phase transitions) — Bot now plays economically correctly for Ante 3+.
Fix 3 (Sell gate) — Bot stops dumping good jokers. Requires Fix 2 to be meaningful.
Fix 4 (xMult valuation) — Bot now correctly values late-game multiplicative pieces. Requires Fix 3 so those jokers don't get sold immediately after buying.
Fix 5 (Discard alignment) — Board efficiency improvement, requires Fix 4 so the hand type target aligns with the joker build.
Fix 6 (Blind scaling) — Polish pass. Prevents the pivot gate from freezing up at Ante 6-8.

Fixes 1–4 alone should get you to Ante 6 reliably. All six together should clear Ante 8.
