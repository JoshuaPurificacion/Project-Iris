# Project Iris - Game Logic Completion Plan

## Objectives
- Stabilize game loops and navigation so Iris can reliably start/stop and switch between all supported modes (Balatro, Quiz, Micro RPG, Idle/Default, Exhibit).
- Resolve known logical bugs before adding new behaviors; keep changes auditable and tested.
- Establish a clear workflow for implementing and validating future game logic changes.

## Known Issues (to fix first)
- Balatro: possible race on shared `_balatro_running`/`_game_controller`; risk of concurrent access ([skills/balatro_skill.py](skills/balatro_skill.py#L32)).
- Balatro: stale controller on stop; restart may reuse corrupt state ([wake_up.py](wake_up.py#L107-L118)).
- Balatro: action tag regex too strict; LLM response variants ignored, causing hangs ([skills/balatro_skill.py](skills/balatro_skill.py#L77-L106)).
- Balatro: hand mismatch trim still feeds trimmed indices into plays; can retry-loop on index rejection ([skills/balatro_skill.py](skills/balatro_skill.py#L17-L27)).
- Balatro: state-change detection is shallow (string concat); subtle nested changes missed ([skills/balatro_skill.py](skills/balatro_skill.py#L22-L27)).
- Quiz: avatar callback timing can race with destroyed window (design smell, currently try/except) ([skills/quiz_skill.py](skills/quiz_skill.py#L56-L66)).
- Micro RPG: defend action always 0.5x, making it strictly weaker than attack/heal loops ([games/micro_rpg.py](games/micro_rpg.py#L309-L312)).
- Cosmetic: filename/header mismatch in RPG mode ([modes/iris_rpg.py](modes/iris_rpg.py#L1)).

## Workflow to Complete Game Logic
1. **Balatro Stabilization**
   - Add thread-safe guard around `_balatro_running` and `_game_controller` (start/stop lifecycle, single owner).
   - On stop, clear controller/state and confirm server shutdown; block restart until teardown completes.
   - Relax action parsing: accept bracketed/unbracketed tags, case-insensitive, trim whitespace; add fallback prompts.
   - Replace shallow state snapshots with hashed/structured comparisons (e.g., hands_left, shop state, round id).
   - Add robust hand-count reconciliation: re-fetch state when mismatch occurs; do not reuse trimmed indices.
2. **Balatro Play/Shop Logic (future implementation)**
   - Introduce configurable shop strategy and safer bankrupt buffer; auto-buy only when value-positive.
   - Improve discard/hand targeting with stronger intra-hand scoring; align discards to target hand type.
   - Add telemetry/logging toggle for decisions and retries.
3. **Mode Navigation & Lifecycle**
   - Centralize mode start/stop contracts (Balatro, Quiz, Micro RPG, Exhibit, Default) to ensure windows and threads are cleaned before switching.
   - Add idempotent checks before showing UI windows to avoid flicker or double-creation.
4. **Quiz Stability**
   - Harden avatar callback scheduling: short-circuit if window destroyed; centralize avatar lifecycle helpers.
   - Add minimal unit/integration test to simulate window teardown mid-quiz.
5. **Micro RPG Balance**
   - Re-tune defend: scale with defense stat or add mitigation/randomness so it is situationally optimal.
   - Add small simulation tests to ensure defend is chosen in appropriate scenarios.
6. **Testing & Telemetry Layer**
   - Expand `test_balatro.py` and add new tests for shop/tag parsing, state-change detection, and lifecycle resets.
   - Add tests for quiz avatar teardown and micro_rpg defend math.
   - Add structured logs for mode transitions and error cases.

## Execution Order
1) Fix critical Balatro lifecycle/parse/state issues (items 1–5).  
2) Stabilize navigation contracts across modes and UI.  
3) Patch Quiz avatar lifecycle.  
4) Rebalance Micro RPG defend.  
5) Add/extend tests and logging.  
6) Design and implement Balatro shop/hand strategy (post-stabilization).

## Definition of Done
- No known critical race conditions or stale controllers when starting/stopping modes.
- Action parsing tolerant to reasonable LLM output variance; bot does not hang awaiting commands.
- State-change detection drives timely actions; retries bounded with clear logs.
- Quiz and Micro RPG run without lifecycle errors; defend is competitively viable.
- Tests cover lifecycle, parsing, and key game decisions; all green.
- Logs clearly show mode transitions, decisions, and errors for debugging.
