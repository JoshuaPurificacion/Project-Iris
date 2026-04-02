Allow `[buy_pack N]` plus pack-opening actions so the LLM can open Buffoon/Standard/Arcana/Celestial/Spectral packs, choose optimal cards (or skip), and supply targets for consumables, reusing BalatroBot `buy`/`pack` RPCs and surfacing pack contents in the prompt. Keep voucher support explicit and card buys unchanged. Collapse pack interaction to two tags to avoid hallucinations.

Steps
1) Lift prompt ban and document pack flow in modes/balatro.py: permit buying packs, but dynamically inject a rule during the "shop" state: "Do not buy Booster Packs unless bankroll > $35, or you are desperate for a Buffoon joker; keeping $25 interest takes priority." List allowed tags minimally: `[buy_pack N]`, `[choose_pack CARD_IDX TARGET_1 TARGET_2]` (targets optional), `[skip_pack]`, `[continue]`. Explain immediate-use vs deck-add behavior and the pack-open state. Keep `[buy_shop N]` (cards/consumables) and `[buy_voucher]` unchanged and clearly separated.
2) Expose pack data to the LLM via skills/balatro_client.py: include packs/pack details in BalatroState and get_context_for_llm() (type, size, options, keys, costs) while keeping unrelated hand data hidden when not needed.
3) Add RPC helpers in skills/balatro_client.py: categorized payloads only — `buy(card=idx)`, `buy(voucher=1)`, `buy(pack=idx)`; `pack(card=idx|targets=[...]|skip=true)`. No global index math. Reuse existing error handling with shop/next_round.
4) Rewrite the action parser in skills/balatro_skill.py: upgrade the regex to capture a variable list of arguments (e.g., `match = re.search(r"\[([a-zA-Z_]+)(.*?)\]", response)` then split by spaces to get integers). Accept pack-specific tags (`buy_pack`, `choose_pack`, `skip_pack`); safely handle missing args and variable-length targets. Reject unknown tags to reduce hallucinations. Keep legacy numeric shop buys working.
5) Branch game loop in skills/balatro_skill.py: intelligently detect pack states with `if "pack" in current_state.lower() or "booster" in current_state.lower():` to cover `SMODS_BOOSTER_OPENED` or specific strings like `Tarot_Pack` etc. In pack-open state, only allow `choose_pack`/`skip_pack`; add one retry then auto-skip to avoid deadlocks.
6) Craft simple pack prompts in skills/balatro_skill.py: pass the raw pack JSON to the LLM. Inject a clear, lightweight directive: "PACK OPENED. Look at the pack cards in the JSON. You can use [choose_pack CARD_IDX TARGETS] or [skip_pack]. Remember your synergies." Crucially, also extract and inject the "Picks Remaining" (e.g., `choices_left`) from the gamestate into the prompt so the LLM doesn't skip Mega packs early. Let the LLM do the thinking.
7) Target plumbing & Auto-Skip Fallback: when consumables need targets, emit `pack(card=…, targets=[…])` after checking target counts. If the LLM issues `[choose_pack]` but forgets or provides invalid targets, do NOT try to artificially target cards (which can destroy a run). Instead, log the error and immediately execute the `[skip_pack]` RPC call to fail predictably and prevent a soft-lock.
8) Testing in test_balatro.py: cases for `[buy_pack 1]`, `[choose_pack 1]` with and without targets, multiple arguments regex parsing, `[skip_pack]`, auto-skip fallback logic, broadly matched pack states, and guardrail retries; fixture gamestates for each pack type including Mega packs (multiple choices). Add negative cases for missing/invalid args.
9) Docs & attribution in README.md: describe new tags/flow and cite references.

Verification
- Unit tests for pack parsing/dispatch and mock RPC calls (`buy` with `pack`, `pack` with `card/skip/targets`).
- Integration: simulate SMODS_BOOSTER_OPENED and ensure correct `pack` RPC selection/skip behavior.
- Prompt inspection: ensure pack contents and allowed actions are listed; no lingering bans on packs.
- Manual dry-run against BalatroBot: `[buy_shop pack 1]` -> pack prompt -> `pack(card=idx)`/`pack(skip=true)` succeeds.

Decisions/assumptions
- Use BalatroBot `buy(pack=idx)` + `pack(card|targets|skip)` per official API docs.
- Surface pack contents to the LLM (lift current exclusion in get_context_for_llm).
- Auto-skip after one invalid pack action to keep runs unblocked.

Further considerations
- If BalatroLLM strategy/memory/gamestate docs live elsewhere, fetch and fold in their heuristics; otherwise proceed with in-house rules.
- Optional telemetry: track most-used hand to improve Celestial planet picks.
- Budget-awareness in prompts: allow skipping/deferring pack buys when saving for jokers/vouchers.
