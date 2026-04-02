# Balatro File Map

This file describes the Balatro-specific files in Project Iris and what each one does.

## Runtime and Entry Points

### `skills/balatro/__init__.py`
Exports the Balatro session lifecycle helpers so the package can be imported cleanly.

### `skills/balatro_skill.py`
Legacy compatibility shim that keeps older Balatro entrypoint callers working while routing into the current session-based implementation.

### `modes/balatro.py`
Mode adapter for switching Iris into Balatro behavior without exposing the internal session plumbing to callers.

## Core Balatro Engine

### `skills/balatro/session.py`
Main Balatro runtime controller. Builds planner context, defines the planner schema, runs the session loop, parses planner output, and coordinates actions.

### `skills/balatro/router.py`
Phase router for Balatro. Chooses the current game phase and builds phase-specific guidance for the planner.

### `skills/balatro/prompt_builder.py`
Constructs the final prompt/context sent to the LLM, including state constraints, boss rules, run profile data, and recent actions.

### `skills/balatro_client.py`
Client wrapper for the Balatro JSON-RPC API. Fetches live game state and sends action commands back to the game.

## Action Handlers

### `skills/balatro/actions/base.py`
Shared base class for Balatro actions. Provides common validation, logging, and execution helpers.

### `skills/balatro/actions/blind.py`
Handles blind-selection behavior and blind-related actions such as selecting or skipping blinds.

### `skills/balatro/actions/continue_action.py`
Implements the fallback/continue action used when the bot should do nothing and advance state.

### `skills/balatro/actions/pack.py`
Handles pack and booster interactions, including choosing cards or skipping packs.

### `skills/balatro/actions/play.py`
Handles playing and discarding cards from the hand during combat/scoring phases.

### `skills/balatro/actions/shop.py`
Handles shop-specific actions such as buying Jokers, vouchers, packs, rerolls, and other shop purchases.

### `skills/balatro/actions/utility.py`
Handles utility actions like selling Jokers or consumables, using consumables, and rearranging inventory when needed.

### `skills/balatro/actions/__init__.py`
Exports the action registry package so the session can load Balatro actions as a group.

## Strategy and Heuristics

### `skills/balatro_bot/modules/algorithms.py`
Contains the scoring and heuristic engine for Balatro. Evaluates hands, Jokers, consumables, shop items, and synergy-based decisions.

### `skills/balatro_bot/modules/balatro_telemetry.py`
Writes Balatro decision telemetry so runs can be analyzed after the fact.

## Tests

### `tests/test_balatro.py`
General Balatro behavior tests and compatibility checks.

### `tests/test_balatro_session.py`
Tests the Balatro session lifecycle, planner schema behavior, and related session-level guarantees.

### `tests/test_balatro_router_prompt.py`
Tests the router guidance and prompt context generation for shop decisions and synergy evaluation.

### `tests/test_balatro_algorithms.py`
Tests the Balatro heuristics and scoring logic in the algorithm layer.

### `tests/test_balatro_integration.py`
Integration tests for the full Balatro flow across state ingestion, planning, and execution.

## Debugging and Inspection Scripts

### `scripts/inspect_jokers.py`
Quick snapshot tool that prints the current owned Jokers and shop Jokers to a text file for debugging and prompt verification.

### `scripts/test_api_jokers.txt`
Captured sample output from the inspector script showing the raw Joker and shop JSON used for prompt work.

### `scripts/test_api.py`
API fuzzer and state inspector for Balatro debugging.
- Default mode: fires 14 probe shapes to discover correct RPC method/payload for a given action.
- `--inspect` mode: read-only dump of live `consumeables` and `hand` card JSON, used to
  discover exact field names and nesting (e.g. confirmed `modifier.enhancement` and `value.effect`).
- Results written to `scripts/test_api_results.txt` and `scripts/test_api_inspect.txt`.

### `scripts/test_api_inspect.txt`
Saved output from API inspection runs, useful for verifying raw state payload structure.

## Planning Docs

### `docs/BALATRO_BOT_ARCHITECTURE.md`
High-level explanation of the Balatro bot architecture, strategy, and data flow.

### `docs/BALATRO_DEVELOPMENT_LOG.md`
Chronological Balatro-specific engineering notes, decisions, and validation history.

### `docs/plans/implementation_plan_balatroOrobustness.md`
Implementation plan for Balatro robustness work.

### `docs/plans/PLAN_GAME_LOGIC.md`
Balatro/game logic planning document.

### `docs/plans/plan-balatroShop.prompt.md`
Prompt-design notes for Balatro shop behavior.

### `docs/plans/plan-boosterPacks.prompt.md`
Prompt-design notes for booster pack behavior.
