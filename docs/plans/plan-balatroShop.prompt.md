## Plan: Balatro shop routing and 1-based index safety

Goal: refine shop-phase handling so Iris can call category-specific buys (jokers, voucher, packs/consumables) with explicit 1-based indices that match the Lua/Steamodded API, plus clearer prompt guidance and tests.

**Steps**
1. Prompt/tag alignment: update the Balatro mode prompt to describe the shop layout and allowed tags — [buy_shop X] for jokers/consumables (1-based), [buy_voucher] for the single voucher, [buy_pack X] for booster packs (1-based), [continue] to leave. Keep the 1–2 sentence streamer voice rule. Add the recommended shop-phase blurb so Iris doesn’t hallucinate indices.
2. Action parser + regex: expand parsing to recognize buy_voucher and buy_pack alongside buy_shop/continue/play_hand/discard/skip; use the case-insensitive pattern \[(buy_shop|buy_voucher|buy_pack|continue|play_hand|discard|skip)(?:\s+(\d+))?\]. Allow missing numeric args only when the target is unambiguous (single voucher) and enforce 1-based expectations in logs/errors.
3. Dynamic index resolver (1-based): in shop state, compute indices from container lengths, not hardcoded slots — buy_shop X → X (1 ≤ X ≤ len(shop.cards)); buy_voucher → len(shop.cards) + 1; buy_pack X → len(shop.cards) + len(vouchers.cards) + X. Clamp to available counts, no-op with clear log if missing, and tolerate clearance-sale extra slots or absent containers.
4. Purchase execution: call the buy endpoint with the resolved 1-based index; on success, optionally refresh state once to reflect removal. Preserve next_round on [continue] and keep existing state guards.
5. Tests: extend unit coverage in test_balatro.py for new tags, regex parsing, and resolver math across varying shop shapes (no voucher, extra voucher slot, packs present/absent, short shops). Guarded integration in test_balatro_integration.py to confirm buy/next_round still succeed against the live bot with 1-based indices. Keep existing lifecycle tests green.
6. Safety/logging: bound retries, emit explicit logs for missing items or out-of-range requests, and ensure stop_balatro teardown still clears controller/running flags.

**Relevant files**
- wake_up.py — start/stop Balatro actions already wired; no change expected unless new tags flow through UI.
- modes/balatro.py — prompt that defines allowed action tags and shop guidance.
- skills/balatro_skill.py — game loop, action parsing/dispatch, shop routing, index resolver.
- skills/balatro_client.py — JSON-RPC client; buy/next_round calls (helper if needed).
- skills/balatro_bot/modules/algorithms.py — hand/discard logic (unchanged, reference only).
- test_balatro.py — unit tests for lifecycle, parsing, resolver.
- test_balatro_integration.py — live bot smoke; optional shop/buy check with 1-based indices.

**Verification**
1. Run unit suite: python -m pytest test_balatro.py
2. If BalatroBot available: BALATRO_INTEGRATION=1 python test_balatro_integration.py (or BALATRO_LOOP=1) to confirm buy/next_round paths.
3. Manual sanity: enter shop in Balatro mode, issue [buy_voucher] or [buy_pack 1]/[buy_shop 2], see purchase succeed, then [continue] to Blind Select.

**Decisions/assumptions**
- API is strictly 1-based; never send 0-based indices.
- Voucher is a single item; index = len(shop.cards) + 1 when present.
- Packs use index = len(shop.cards) + len(vouchers.cards) + X; shop.cards may include consumables.
- Keep [buy_shop X] for jokers/consumables; add category-specific tags without breaking existing ones.