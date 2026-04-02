# Balatro Bot Architecture

This document describes the current Balatro runtime architecture used by Project Iris.

## Overview

The bot uses a modular loop with three main layers:

1. State + Context Layer
- Reads live JSON-RPC game state.
- Builds planner-safe context with phase constraints and strategy hints.

2. Planning Layer
- Uses a strict JSON planner output contract.
- Produces a single action + indices + short reasoning each tick.

3. Execution Layer
- Routes to typed action handlers.
- Applies guardrails to prevent invalid or low-value decisions.

## Runtime Flow

1. `BalatroSession` refreshes game state each tick.
2. `PhaseRouter` determines current phase and allowed actions.
3. `PlannerContextBuilder` composes state, constraints, and hints.
4. Planner returns JSON action.
5. Action dispatch executes through registry-backed handlers.
6. Result is logged to memory and telemetry.
7. Persona reaction runs asynchronously.

## Key Components

### Session Core
- `skills/balatro/session.py`
- Responsibilities:
  - Tick loop and softlock escape handling
  - Planner context assembly
  - Guided options generation
  - Shop strategy block generation

### Phase Router
- `skills/balatro/router.py`
- Responsibilities:
  - Phase detection (`menu`, `blind`, `play_discard`, `shop`, `pack`, `round_eval`)
  - Phase-specific guidance and allowed actions

### Prompt Builder
- `skills/balatro/prompt_builder.py`
- Responsibilities:
  - Build final planner context payload
  - Inject state constraints and run profile data
  - Add shop and pack state restrictions

### Action System
- `skills/balatro/actions/`
- Notable handlers:
  - `shop.py`: buy/reroll/start-run actions with economy guardrails
  - `utility.py`: sell/use/rearrange actions with safety checks
  - `play.py`: play/discard execution and retry logic

#### `UseConsumableAction` Guardrail Tiers

**Tier 1 — Joker dependency check:**
Blocks consumables whose effect description references `"joker"` when no Jokers are owned,
unless the effect creates or spawns one. Reads from `consumable.value.effect`.

**Tier 2 — Wasted enhancement check:**
Blocks targeting a hand card that already has the same enhancement the Tarot applies.
Only active when `consumable.modifier.enhancement == "ENHANCE"`.
Parses the target enhancement from `consumable.value.effect` via regex `to (\w+) Cards?`,
then compares to each target card's `hand_card.modifier.enhancement`.
Returns a specific error message back to the LLM so it can replan with a valid target.

### API Client
- `skills/balatro_client.py`
- Responsibilities:
  - JSON-RPC contract wrapper
  - Typed game-state model
  - LLM context serialization
  - Shop advisor enrichment in context payload

#### Confirmed API Contract for Consumable Use

| Action | Method | Payload |
|---|---|---|
| Use consumable (no targets) | `use` | `{"consumable": idx}` |
| Use consumable (with targets) | `use` | `{"consumable": idx, "cards": [t...]}` |
| Choose pack card (with targets) | `pack` | `{"card": idx, "cards": [t...]}` |

The key for target hand cards is `"cards"`, not `"targets"`. Verified via API fuzzer (probe F2).
The `select` endpoint is state-gated to `BLIND_SELECT` only and must never be called during play.

### Algorithms / Heuristics
- `skills/balatro_bot/modules/algorithms.py`
- Responsibilities:
  - Hand evaluation
  - Consumable and pack option suggestions
  - Joker advisor labels
  - Theoretical shop impact math and safe-sell detection

## Shop Optimization Model

Current shop behavior combines deterministic guardrails with planner flexibility.

### Theoretical Impact
The algorithm estimates score deltas before committing to Joker changes:
- `estimate_best_score(...)`
- `evaluate_shop_joker_impact(...)`
- `identify_safe_sell_jokers(...)`

### Shop Strategy Block
`session.py` injects a `SHOP STRATEGY` section into planner context during shop state with:
- Money, reserve, surplus, reroll cost
- Current Joker keep/sell risk labels
- Item-level projected impact and replacement hints

### Guardrails
- Buy guardrails prevent low-value reserve breaks unless impact justifies it.
- Reroll guardrails prevent wasteful or unsafe rerolls.
- Sell guardrails block selling high-impact Jokers unless replacing with stronger options.

## Validation Strategy

Primary test coverage lives in:
- `tests/test_balatro.py`
- `tests/test_balatro_session.py`
- `tests/test_balatro_algorithms.py`
- `tests/test_balatro_router_prompt.py`

These validate schema handling, session lifecycle, routing constraints, and algorithmic decisions including shop impact and safe-sell logic.

## Notes

- The planner remains the decision-maker, but guardrails enforce safety.
- Architecture is designed to keep behavior explainable and debuggable through logs + telemetry.
- Shop optimization now explicitly reasons about score impact rather than relying only on string heuristics.
