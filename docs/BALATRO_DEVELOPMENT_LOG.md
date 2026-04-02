# Balatro Development Log

This log tracks Balatro-specific implementation decisions, prompt changes, tests, and debugging notes.

---

## Session Date: April 2, 2026

### Focus: Shop Synergy Evaluation and Joker Effect Reasoning

### Context

Captured live Balatro state snapshots showed that both owned Jokers and shop Jokers include readable `value.effect` text, which is suitable for direct LLM comparison during shop decisions.

Example live effects captured:
- Owned Joker: `Played face cards give +30 Chips when scored`
- Shop Joker: `This Joker gains +3 Mult when any Booster Pack is skipped (Currently +0 Mult)`
- Shop Joker: `+100 Chips if played hand contains a Three of a Kind`

### Decisions

1. Keep the planner output JSON flat for compatibility with existing downstream parsing.
2. Add `synergy_evaluation` as a top-level field so it is generated before `action`.
3. Make shop guidance explicitly require the model to compare candidate Joker effects against currently owned Joker effects and the current build direction.
4. Preserve `value.effect` in the state JSON and prompt context so the model reads the actual card text instead of relying on a lookup table.

### Implementation Notes

- Updated `skills/balatro/session.py` to add `synergy_evaluation` to `PlannerOutput` and the system prompt contract.
- Updated `skills/balatro/router.py` to add a shop decision rule requiring synergy evaluation before purchase actions.
- Added shop-context coverage in `skills/balatro/prompt_builder.py` so the planner gets stronger availability constraints.
- Added support for both `consumeables` and `consumables` payload spellings in the Balatro code paths that read or manipulate consumable cards.

### Validation

- Added targeted tests for router guidance, planner schema compatibility, and real Joker effect text in prompt context.
- Ran the Balatro-focused test suite successfully after the changes.

### Useful Files

- [docs/BALATRO_FILE_MAP.md](BALATRO_FILE_MAP.md)
- [skills/balatro/session.py](../skills/balatro/session.py)
- [skills/balatro/router.py](../skills/balatro/router.py)
- [skills/balatro/prompt_builder.py](../skills/balatro/prompt_builder.py)
- [tests/test_balatro_router_prompt.py](../tests/test_balatro_router_prompt.py)
- [tests/test_balatro_session.py](../tests/test_balatro_session.py)
- [tests/test_balatro_algorithms.py](../tests/test_balatro_algorithms.py)
### Consumable Usage Fixes

### Context
The bot was ignoring the majority of consumable items because `evaluate_consumable_plays` in `algorithms.py` relied on a hardcoded, incomplete whitelist. If a consumable wasn't recognized (which excluded ~60+ Tarot and Spectral cards), it never appeared as a "Guided Option," so the model would safely ignore it. Furthermore, an empty consumables list posed a latent `NameError` crash risk.

### Implementation Notes
- Fixed a `NameError` in `evaluate_consumable_plays` by explicitly returning `[]` when the state has no consumables.
- Expanded the No-Target Whitelist to dynamically match Planets (e.g., checking `is_planet` or prefix strings) and several additional standard utility Spectral cards.
- Added generalized smart target logic for Enhancer and Destructor cards (Tarots and Spectrals):
  - **Enhancers & Suit Changers** (e.g., Empress, Lovers, Justice, Sun): Target the highest-ranking cards in hand.
  - **Destructors & Modifiers** (e.g., Hanged Man, Tower, Hex): Target the lowest-ranking cards in hand.
  - **Death**: Retained the specific sequence logic to target the lowest and duplicate the highest.
- Considerably broadened the evaluation scope from ~20 to over 60 recognizable consumable cards.

### Validation
- Added explicit unit tests in `tests/test_balatro_algorithms.py` to verify that `evaluate_consumable_plays` correctly handles empty lists, Enhancers (targeting highest cards), and Destructors (targeting lowest cards).
- All tests verify properly.

---

## Session Date: April 2, 2026

### Focus: Shop Reroll Softlock Prevention

### Context

Live run logs showed a shop loop where the planner repeatedly selected `reroll` after funds were exhausted. The API correctly returned `NOT_ALLOWED` with insufficient funds details, but the planner context was missing key cost visibility and was receiving generic failure text.

Observed loop pattern:
- Agent cashed out, bought one shop item, then rerolled.
- Funds dropped to `$0`.
- Planner selected `reroll` again.
- API returned `Not enough dollars to reroll. Available: 0, Required: 6`.
- Loop repeated until softlock stall handling forced `proceed_next`.

### Root Cause

1. `reroll_cost` from game state was not modeled in `RoundInfo`, so typed LLM context did not reliably expose reroll economics.
2. `RerollAction` read `reroll_cost` from `shop` instead of `round`, which is the wrong state path.
3. Reroll logs and persona reasoning strings had missing interpolation (`for .`), reducing clarity.
4. Failed rerolls returned a generic error instead of surfacing the API's precise denial reason.

### Implementation Notes

- Updated `skills/balatro_client.py`:
  - Added `reroll_cost: int = 0` to `RoundInfo` so reroll pricing is present in typed state context.

- Updated `skills/balatro/actions/shop.py` in `RerollAction.execute`:
  - Read cost from `raw_state["round"]["reroll_cost"]`.
  - Fixed log and reasoning output to include `${reroll_cost}`.
  - Included `client._last_error` in failure `error` and `persona_reasoning` so downstream planner memory sees the exact API denial.

### Validation

- Static diagnostics: no errors in modified files.
- Focused tests passed:
  - `tests/test_balatro.py`
  - `tests/test_balatro_session.py`
  - Result: `7 passed`.

### Useful Files

- [skills/balatro_client.py](../skills/balatro_client.py)
- [skills/balatro/actions/shop.py](../skills/balatro/actions/shop.py)
- [tests/test_balatro.py](../tests/test_balatro.py)
- [tests/test_balatro_session.py](../tests/test_balatro_session.py)

---

## Session Date: April 2, 2026

### Focus: Checkered-Lock Strategy, Scaling Jokers, and Shop Upgrades

### Context

The Balatro bot was being pushed toward a deterministic Checkered-deck game plan so the rest of the heuristics could assume a Flush-oriented build. That required the scoring layer to reason over the live state instead of base card text alone, especially for Jokers that scale with cards, suit composition, or held resources.

### Fixes

1. Forced Checkered deck selection in the Balatro routing and shop decision flow so the run stays aligned with the Flush plan.
2. Extended Joker evaluation so scaling pieces are scored from the actual game state instead of a static baseline.
3. Added sell-to-upgrade guidance in shop strategy output so the bot can replace weaker Jokers when a stronger scaling option appears.
4. Increased consumable prioritization for Jupiter because Checkered runs benefit most from leveling Flush.
5. Added tests covering the new scaling and consumable heuristics.

### Implementation Notes

- Updated `skills/balatro_bot/modules/algorithms.py` to evaluate scaling Jokers using live state inputs and to bias consumables toward Jupiter.
- Updated `skills/balatro/session.py` so shop prompts can emit explicit `sell_joker` then `buy_shop` recommendations.
- Updated `skills/balatro/router.py`, `skills/balatro/actions/shop.py`, and `skills/balatro/actions/utility.py` so the Checkered strategy and sell-to-upgrade path stay consistent across phases.
- Added focused Balatro tests to verify the new scoring and shop guidance behavior.

### Validation

- Ran the targeted Balatro test suite successfully after the changes.

### Useful Files

- [docs/BALATRO_BOT_ARCHITECTURE.md](BALATRO_BOT_ARCHITECTURE.md)
- [docs/BALATRO_FILE_MAP.md](BALATRO_FILE_MAP.md)
- [skills/balatro_bot/modules/algorithms.py](../skills/balatro_bot/modules/algorithms.py)
- [skills/balatro/session.py](../skills/balatro/session.py)
- [skills/balatro/router.py](../skills/balatro/router.py)
- [skills/balatro/actions/shop.py](../skills/balatro/actions/shop.py)
- [skills/balatro/actions/utility.py](../skills/balatro/actions/utility.py)
- [tests/test_balatro_algorithms.py](../tests/test_balatro_algorithms.py)

---

## Session Date: April 2, 2026

### Focus: Joker-Impact Shop Optimization (Final Update)

### Summary

This update added theoretical score-impact evaluation for shop Jokers, safe Joker replacement logic, and stronger reroll/sell guardrails so surplus money gets converted into real upgrades instead of softlocking on failed buy/reroll loops.

### Key Changes

1. Added projected impact helpers in `skills/balatro_bot/modules/algorithms.py`:
  - `estimate_best_score`
  - `evaluate_shop_joker_impact`
  - `identify_safe_sell_jokers`
2. Added voucher evaluation helper `score_voucher_value`.
3. Upgraded `score_joker_value` to optionally use hand/deck-aware impact math.
4. Injected `SHOP STRATEGY` context in `skills/balatro/session.py` with reserve/surplus, keep/sell risk, and replacement hints.
5. Updated `skills/balatro/actions/shop.py`:
  - Full-slot buy failures now provide sell-first upgrade hints.
  - Reroll is blocked when unsafe or when strong items are already present.
6. Updated `skills/balatro/actions/utility.py`:
  - `sell_joker` blocks selling high-impact Jokers unless replacing with stronger visible shop options.
7. Updated guidance text in `skills/balatro/router.py` and constraints in `skills/balatro/prompt_builder.py`.
8. Updated `skills/balatro_client.py` shop advisor serialization to pass richer context to scorer callbacks.

### Validation

- Added targeted tests in `tests/test_balatro_algorithms.py` for impact and safe-sell behavior.
- Ran:
  - `tests/test_balatro.py`
  - `tests/test_balatro_session.py`
  - `tests/test_balatro_algorithms.py`
- Result: **12 passed**.

---

## Session Date: April 2, 2026

### Focus: Booster Pack Softlock Fix

### Context

The agent was getting stuck in an INVALID_STATE error loop by trying to execute a consumable during booster pack selection (e.g. after opening a Jumbo Spectral Pack). The game does not support the use action while in the pack selection frames.

### Root Cause

Consumable usage (`use_consumable`) was globally inherited via utility actions during booster pack phases. Therefore, the LLM thought it was a valid action and attempted to use it.

### Implementation Notes

A 3-layer defensive fix was applied:

- **Router Level:** Updated `skills/balatro/router.py` to explicitly disable utility actions (`utility_actions=[]`) during booster drafts within `_get_pack_directive`.
- **Action Validation Level:** Updated `skills/balatro/actions/utility.py` to add an early-return guard rail in `UseConsumableAction.execute()` if "pack" or "booster" is in the state.
- **Prompt Guidance Level:** Updated `skills/balatro/prompt_builder.py` to inject `state_constraints` for pack and shop phases to explicitly warn the planner against using invalid utility actions.

### Validation

- The pytest suite (`test_balatro_session.py`) completed and passed successfully, confirming the regressions were avoided.

### Useful Files

- [skills/balatro/router.py](../skills/balatro/router.py)
- [skills/balatro/actions/utility.py](../skills/balatro/actions/utility.py)
- [skills/balatro/prompt_builder.py](../skills/balatro/prompt_builder.py)
- [tests/test_balatro_session.py](../tests/test_balatro_session.py)

---

## Session Date: April 2, 2026

### Focus: Consumable Targeting Softlock — Root Cause, API Discovery, and Guardrails

---

### Problem

The bot softlocked when using targeted consumables (e.g. The Hanged Man, Death, The Magician).
The `use()` and `pack()` macros in `balatro_client.py` were calling `select({"cards": targets})`
before the actual `use` call. The `select` endpoint is state-gated to `BLIND_SELECT` only.
In `PLAY` and `SELECTING_HAND` states, it returned:

```
Method 'select' requires one of these states: BLIND_SELECT
```

The bot logged a warning and continued, but the `use` payload was also using the wrong key
(`"targets"` instead of `"cards"`), meaning even if `select` had worked, the targets would have
been silently ignored.

---

### Discovery Method

A systematic API fuzzer was written at `scripts/test_api.py` covering 14 probe shapes across
6 groups (bare use, toggle_highlight, highlight, select_cards, select variants, and alternate
use payload keys). Key findings from two fuzz runs:

- `toggle_highlight`, `highlight`, `select_cards` — **do not exist** in the Lua mod at all
- `select` — **state-gated to BLIND_SELECT**, cannot be called in play/hand states
- `use {"consumable": C, "targets": T}` — silently ignored `targets`, no error, no effect
- `use {"consumable": C, "cards": T}` — **correct shape**, server accepted it, game advanced

The winning probe was **F2**: `use({"consumable": 0, "cards": [0, 1]})`.


### Fix 1 — `balatro_client.py`: Remove broken `select` pre-call, correct payload key

**`use()` method:**
- Removed: `self._call("select", {"cards": targets})` pre-call and its warning log
- Changed: payload key from `"targets"` → `"cards"` (verified correct via fuzz)
- Result: `use({"consumable": C, "cards": [T...]})` — single call, no state dependency

**`pack()` method:**
- Removed: same broken `select` pre-call pattern
- Added: `params["cards"] = targets` so targets are forwarded in the pack payload
- Docstrings on both methods updated to document the confirmed API contract

---

### Fix 2 — `utility.py`: Post-use state refresh to prevent index shift

After a consumable is used (consumed), the list shrinks. If the bot had two consumables
at indices 0 and 1 and used index 0, what was index 1 is now index 0.

Without a refresh, `tick_context.raw_state` holds the stale pre-use list for the rest of
the tick — stale bounds checks, stale labels in logs, and incorrect indices for any
downstream read.

**Change:** After a successful `use()` call in `UseConsumableAction.execute()`:
- `ctx.controller.refresh_state()` is called immediately
- `ctx.tick_context.update_raw_state(...)` overwrites the stale snapshot
- Remaining consumable list is logged (count + labels)

This mirrors the existing pattern in `DiscardAction`, which already refreshed after discard.

---

### Fix 3 — `utility.py` Tier 1 bug: wrong key path for joker-guard check

The existing joker-guard (Tier 1) was reading:
```python
card_effect = consumables[idx].get("effect", "").lower()
```

But the live API payload nests `effect` under `value`:
```json
{ "value": { "effect": "Increases rank of up to 2 selected cards by 1" } }
```

Fixed to:
```python
card_effect = (consumable.get("value") or {}).get("effect", "").lower()
```

This fix was found via the `--inspect` mode added to `scripts/test_api.py`.


### Fix 4 — `utility.py` Tier 2 guardrail: block wasting an enhancement Tarot

**Problem:** The bot could target a card that already has the same enhancement the Tarot applies
(e.g. using The Magician on a card already marked `LUCKY`). The API accepts the call, the
consumable is consumed, and nothing useful happens.

**API payload structure confirmed via `--inspect`:**
```json
Consumable:
  "modifier": { "enhancement": "ENHANCE" }     ← signals this Tarot applies an enhancement
  "value": { "effect": "Enhances 2 selected cards to Lucky Cards" }

Hand card (already enhanced):
  "modifier": { "enhancement": "LUCKY" }        ← existing enhancement on the card
  "modifier": []                                 ← base card, no enhancement (safe target)
```

**Two helpers added to `utility.py`:**

- `_extract_enhancement_from_effect(effect: str) → str | None`
  Parses the target enhancement name from the consumable's effect string.
  Regex: `to (\w+) Cards?` → e.g. `"Enhances 2 selected cards to Lucky Cards"` → `"LUCKY"`

- `_card_modifier_enhancement(card: dict) → str | None`
  Safely reads `modifier.enhancement` from a hand card.
  Handles the dual-format: `modifier` is either `{"enhancement": "LUCKY"}` or `[]` for base cards.

**Guardrail logic (Tier 2) in `UseConsumableAction.execute()`:**
1. Check if `consumable.modifier.enhancement == "ENHANCE"` (only runs for enhancement Tarots)
2. Extract the target enhancement name from `value.effect`
3. For each target index, read the hand card's `modifier.enhancement`
4. If any target already has that enhancement → block with a clear error message fed back to the LLM:
   `"Wasted use blocked: Lucky Card is already 'LUCKY' enhanced. Choose cards without that enhancement."`

---

### API Contract Reference (confirmed via fuzz + inspect)

| Field | Location | Example |
|---|---|---|
| Consumable effect description | `consumable.value.effect` | `"Enhances 2 selected cards to Lucky Cards"` |
| Consumable type signal | `consumable.modifier.enhancement` | `"ENHANCE"` or `"ROUND BONUS"` |
| Hand card existing enhancement | `hand_card.modifier.enhancement` | `"LUCKY"` (or `modifier: []` = none) |
| Hand card chip value | `hand_card.value.effect` | `"+10 chips 1 in 5 chance for +20 Mult..."` |
| Correct `use` payload | `{"consumable": idx, "cards": [t...]}` | Verified via probe F2 |

### Validation

No automated tests added this session. Validated by:
- Live fuzz run confirming probe F2 passes with correct game advancement
- `--inspect` dump confirming exact field names and nesting used by the guardrails

### Useful Files

- [skills/balatro_client.py](../skills/balatro_client.py)
- [skills/balatro/actions/utility.py](../skills/balatro/actions/utility.py)
- [scripts/test_api.py](../scripts/test_api.py)
- [scripts/test_api_inspect.txt](../scripts/test_api_inspect.txt)
- [scripts/test_api_results.txt](../scripts/test_api_results.txt)
