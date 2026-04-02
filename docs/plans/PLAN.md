# Balatro Robustness Refactor — Execution Plan

> **How this works:** Each phase is executed one at a time.
> After each phase completes, this file is updated with ✅ and a summary.
> Josh approves before the next phase begins.

---

## Status Legend
- ⬜ Not started
- 🔄 In progress
- ✅ Complete

---

## Phase 1 — WP5: Kill-Switch Refactor `balatro_skill.py`
**Status: ✅ Complete**

### Goal
Replace the `_balatro_running` global boolean flag with a clean `threading.Event()` stop signal.

### Files Touched
- `skills/balatro_skill.py`
- `test_balatro.py`

### Steps
- [x] 1.1 — Added module-level `_stop_event = threading.Event()`
- [x] 1.2 — `start_balatro()`: calls `_stop_event.clear()` before spawning thread; passes event into `game_loop`
- [x] 1.3 — `stop_balatro()`: calls `_stop_event.set()`; removed `_balatro_running` flag
- [x] 1.4 — `game_loop()`: accepts `stop_event` parameter; all flag checks use `stop_event.is_set()`
- [x] 1.5 — Removed `_balatro_running` global entirely (grep confirmed zero references)
- [x] 1.6 — `_balatro_lock` retained for `_game_controller` guarding only
- [x] 1.7 — Updated `test_balatro.py`: all assertions and stubs updated to use `_stop_event`

### Result
Zero `_balatro_running` references remaining. TTS wait loop no longer holds the lock while sleeping.

### Risk: 🟢 Low — pure refactor, zero behavior change

---

## Phase 2 — WP1: API Contract Fix `balatro_client.py`
**Status: ✅ Complete**

### Goal
Align the client's method signatures and payload shapes exactly with the upstream BalatroBot Lua mod API.
Expose `effect` fields so the Planner reads card behavior directly from game state.

### Files Touched
- `skills/balatro_client.py`
- `skills/balatro_skill.py` (caller update)

### Steps
- [x] 2.1 — `move(card, to, area)` → `rearrange(card, to, location)`; API call changed from `"move"` → `"rearrange"`; param `area` → `location`
- [x] 2.2 — `sell()` verified correct shape `{card, area}` — upstream aligned; added alignment comment
- [x] 2.3 — `use()` updated with optional `targets: List[int]` two-call macro:
            Call 1: `_call("select", {"cards": targets})` (only if targets provided)
            Call 2: `_call("use", {"card": idx, "area": "consumeables"})`
- [x] 2.4 — Dynamic Effect Payloads: verified `CardValue.effect` is already in model and not in any
            `excludes` set — effect fields flow through to the Planner by default. Added docstring
            explicitly documenting this intent.
- [x] 2.5 — Updated both `controller.client.move(...)` callers in `balatro_skill.py` → `rearrange(...)`

### Result
Zero `client.move` or `"move"` API call references remaining. Both dispatcher callers confirmed using
`rearrange(from_idx, to_idx, "jokers"|"consumeables")`. Effect fields confirmed flowing through.

### Risk: 🟡 Medium — rename cascades into balatro_skill.py dispatcher ✅ handled

---

## Phase 3 — WP2: Dual-Prompt Core `balatro_skill.py`
**Status: ✅ Complete**

### Goal
Replace the single `iris.chat()` game loop with a stateless dual-prompt architecture:
- **Planner**: silent, ephemeral `ollama.chat()` call at Temperature 0.0, outputs JSON action
- **Persona**: `iris.chat(save=False)` streaming reaction at Temperature 0.8
- **Filler Audio**: non-blocking TTS fires concurrently with Planner network request

### Files Touched
- `skills/balatro_skill.py`

### Steps
- [x] 3.1 — Add `PLANNER_SYSTEM_PROMPT` constant (JSON calculator persona, no streamer flavor)
- [x] 3.2 — Add `FILLER_PHRASES` list (randomized thinking phrases)
- [x] 3.3 — Implement `_call_planner(state_json, memory_str) -> dict`
           - Direct `ollama.chat()` call, `format="json"`, `temperature=0.0`, `stream=False`
           - Lenient JSON parse with markdown strip + fallback default on failure
           - Returns `{"action": str, "indices": List[int], "reasoning": str}`
- [x] 3.4 — Implement `_call_persona(iris, avatar, reasoning, succeeded) -> None`
           - Thin wrapper: `iris.chat(prompt, save=False, avatar=avatar)`
           - Prompt constructed from planner reasoning + API result (succeeded/FAILED)
- [x] 3.5 — Restructure `game_loop()` around 6-step tick:
           1. Refresh state
           2. Fire filler TTS (non-blocking, concurrent)
           3. Call Planner → get `{action, indices, reasoning}`
           4. Execute action via controller
           5. Call Persona with result
           6. Append to `action_memory` deque (action + api_result + error)
- [x] 3.6 — Remove persistent `history_balatro.json` write from the game loop
- [x] 3.7 — Remove old `iris.chat(ephemeral_context=...)` pattern entirely from game loop

### Result
Dual-prompt system fully operational. Planner outputs JSON actions, Persona provides streamer commentary. Guided options integrated into context builder.

### Risk: 🔴 High — entire game loop restructures; test turn-by-turn

---

## Phase 4 — WP3: Extended Guided Selection `algorithms.py`
**Status: ✅ Complete**

### Goal
Upgrade the algorithm layer to pre-compute labeled options so the Planner picks from validated
choices rather than doing raw index math itself.

### Files Touched
- `skills/balatro_bot/modules/algorithms.py`
- `skills/balatro_skill.py` (Planner context builder update)

### Steps
- [x] 4.1 — Upgrade `find_best_hand()` to return top 3 plays as labeled options:
           `{"options": [{"label": "A", "hand_name": ..., "indices": [...], "score": ...}, ...]}`
- [x] 4.2 — Add `evaluate_consumable_plays(consumables, hand_cards) -> List[dict]`
           Detects beneficial Tarot plays (Death, Strength, etc.) as Guided Options
- [x] 4.3 — Add `evaluate_pack_options(pack_cards) -> List[dict]`
           Pre-labels pack choices when state is `pack/booster`
- [x] 4.4 — Update Planner context builder in `balatro_skill.py` to include `"guided_options"` block
- [x] 4.5 — Update all existing callers of `find_best_hand()` for new return shape

### Result
Guided options system integrated. Top 3 hands labeled A/B/C, consumable plays C1-C4, pack options P1-P5. Context builder uses guided_options_str exclusively. Retry logic updated for new return shape.

### Risk: 🟡 Medium — return shape change cascades into skill + tests

---

## Phase 5 — WP4: Telemetry + Tests
**Status: ✅ Complete**

### Goal
Expand telemetry to per-decision logging and rewrite tests for the new architecture.

### Files Touched
- `skills/balatro_bot/modules/balatro_telemetry.py`
- `test_balatro.py`
- `test_balatro_integration.py`

### Steps
- [x] 5.1 — Add `log_decision(action, api_result, error, raw_state)` to `balatro_telemetry.py`
           Logs per-turn records with `"event": "decision"` field
- [x] 5.2 — Add `"event": "death"` field to existing `log_death()` for analytics filtering
- [x] 5.3 — Add Golden State JSON fixture tests for 0-based API shape validation
- [x] 5.4 — Add Planner parse tests: valid JSON / markdown-wrapped / garbage → fallback
- [x] 5.5 — Add `use` macro test: verify two-call sequence fires when `targets` is non-empty
- [x] 5.6 — Add "always select" blind policy test

### Result
- Telemetry now supports both decision and death events for analytics filtering
- Tests updated for new `find_best_hand` return shape (`{"options": [...]}`)
- Guided options tests added (consumable/pack evaluation)
- Planner parsing tests added (markdown stripping, fallback, type coercion)
- Integration tests updated for new return shape

### Risk: 🟢 Low — test-only + additive telemetry

---

## Bug Fix Phases

### Batch A — `balatro_skill.py` Quick Wins
**Status: ✅ Complete**

| Bug | Fix Applied |
|-----|-------------|
| #5 Game Over `save=True` | Added `save=False, use_tools=False` to game-over `iris.chat()` |
| #6 Dead code `_parse_action` | Deleted function entirely; cleaned stale comment |
| #4 Filler TTS every tick | Added `_last_filler_time` float; throttled to max once per 12 seconds |
| #9 Hash soft-lock on failure | Added `last_state_hash = None` in every failure branch (play_hand, discard, buy_shop, buy_pack, choose_pack) |

### Batch B — `algorithms.py` Core Logic Fixes
**Status: ✅ Complete (implemented during Phase 3/4, PLAN.md was not updated at the time)**

| Bug | Fix Applied |
|-----|-------------|
| #2 Discard quota forces high-card discard | No top-up block exists in `find_best_discard` — comment confirms "never force the quota to 5 by raiding protected high cards" |
| #1 Debuff blindness removes cards from candidacy | Fixed: all cards included as candidates in `find_best_hand`; `_evaluate_hand` zeroes chip contribution for debuffed cards without excluding them from hand-type detection |

### Batch C — Persona Fixes
**Status: ✅ Complete (implemented in session.py, PLAN.md was not updated at the time)**

| Bug | Fix Applied |
|-----|-------------|
| #3 Persona repetition (temp 0.35, no memory) | `_call_persona` uses direct `ollama.chat()` at temp 0.7 + `persona_recent` deque with repetition shield prompt |
| #8 Persona blocks loop (sync TTS overlap) | `_call_persona` fires on a daemon thread (non-blocking) |

### Batch D — Advanced Fixes
**Status: ✅ Complete (implemented before PLAN.md was updated)**

| Bug | Fix Applied |
|-----|-------------|
| #7 `choose_pack` target double-conversion | `actions/pack.py` does a single `int(args[0]) - 1` conversion — no double-conversion exists |
| #11 Lethal myopia (plays losing hands) | `prompt_builder.py` `_build_desperate_mode()` injects `*** DESPERATE MODE ***` when `current_best_score * hands_left < score_needed`; wired into `build()` |
| #10 Joker-agnostic algorithm | `session.py` reads `j_four_fingers` / `j_shortcut` from joker list and passes `allows_four_card_hands` / `allows_gaps` into `find_best_hand()`; `_evaluate_hand` handles both flags |

---

## Completion Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Kill-Switch Refactor | ✅ |
| 2 | API Contract Fix | ✅ |
| 3 | Dual-Prompt Core | ✅ |
| 4 | Extended Guided Selection | ✅ |
| 5 | Telemetry + Tests | ✅ |
| Batch A | balatro_skill.py Quick Wins | ✅ |
| Batch B | algorithms.py Core Logic Fixes | ✅ |
| Batch C | Persona Fixes | ✅ |
| Batch D | Advanced Fixes (#7, #10, #11) | ✅ |
