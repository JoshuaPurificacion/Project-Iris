# Balatro Robustness And Reference-Alignment Plan

## Goal Description
Enhance the Balatro skill module by shifting control from a single chatty VTuber persona to a dual-prompt system: a silent, structured, zero-indexed planner that strictly aligns with the upstream BalatroBot reference, and a downstream spoken persona. Separating the execution logic (planner) from the reactionary commentary (persona) will stop LLM hallucinations, fix API contract drift, and open the road for highly structured strategy upgrading and decision telemetry.

## User Review Required
> [!IMPORTANT]
> The biggest UI/UX change is the introduction of a dual-prompt system (planner + persona). The planner will silently interact with the game via function calling, and the persona will react after the fact. Does the current text-to-speech timing need adjustments to ensure the game action completes visually before she speaks?
> Also, I will use `always select` for Boss/Blind selection as a safe default for now as skip/tag logic is incomplete. Is this okay?

## Proposed Changes

### **1. Balatro Client & State Adapter**
Align the internal models and API contracts with the exact upstream BalatroBot schema.

#### [MODIFY] [balatro_client.py](file:///c:/Users/Josh/Documents/Project-Iris/skills/balatro_client.py)
- **API Methods:** Replace custom method definitions with precisely named functions and arguments matching the internal `Action` contract: `play`, `discard`, `buy`, `sell`, `use`, `rearrange`, `pack`, `reroll`, `next_round`, `select`, `skip`, `cash_out`.
- **Indexing:** Enforce strict 0-based indexing for all method signatures.
- **Payload Shapes:** Correct the shapes for `sell` (requires `{card, area}`), `use` (requires `{card, area}`), and change `move()` to `rearrange(card, to, location)`.
- **State Models:** Add missing fields to `BalatroState` and nested components to capture: `round_num`, `deck`, `stake`, `won`, `hands`, `used_vouchers`, `consumables`, `shop` tags, and `packs`.

---

### **2. Prompt Splitting: Planner & Persona**
Decouple game decisions from the VTuber chat context while perfectly masking latency.

#### [MODIFY] [balatro_skill.py](file:///c:/Users/Josh/Documents/Project-Iris/skills/balatro_skill.py)
- **Model Configurations:** 
  - **Planner:** Strict JSON/Structured Output mode, Temperature 0.0, utilizing a highly capable reasoning model for deterministic, structured API outputs.
  - **Persona:** No JSON constraints, Temperature 0.8+, utilizing a very fast model to minimize dead-air before execution.
- **Dual Prompts & Chain of Thought:**
  - **Planner Prompt:** Ephemeral, no persistent chat history. Outputs a structured JSON model (e.g., `action`, `indices`, and `internal_reasoning`).
  - **Persona Prompt:** Spoken character reaction triggered post-planner generation that uses the planner's `internal_reasoning`. The prompt forces a strict two-sentence format: Sentence 1 is the buildup/announcement, Sentence 2 is the post-action reaction.
- **Latency Masking via Sentence Chunking:** 
  - To perfectly sync the game without relying on magic byte-level TTS tags, the playback is split. Sentence 1 plays -> API action executes (while game animations play) -> Sentence 2 plays. This creates a natural, dramatic pause.
- **The "Oops" Interrupt (Error Recovery):** 
  - If the API rejects an action after Sentence 1 has played, the execution thread catches the error, aborts Sentence 2's playback, and immediately injects a high-priority system prompt. The Persona then generates an organic reaction to the failure.
- **0-Based Indices:** Eliminate all +1 / -1 math translation hacks.
- **Action Memory & Hallucination Prevention (History Poisoning):** Currently, because `iris.chat()` runs on a persistent history loop (`history_balatro.json`), a single hallucination (e.g., buying a Tarot card and falsely declaring "I just bought a +Mult Joker!") is permanently saved to the LLM's context. This acts as an echo chamber, poisoning future decisions. We must replace the global persistent history with a lightweight, run-scoped action memory that logs the `attempted_action` alongside the exact `api_status` and `error_message`. The Planner will become completely STATELESS, receiving *only* the current true game state JSON and the short action memory, never its past conversational outputs.
- **Strict Blind Selection:** Enforce deterministic "always select" blind policy to avoid complex Skip EV lookahead.
- **Restore Planner Agency (Play & Discard):** Currently, `balatro_skill.py` completely overrides the LLM's hand selection by intercepting `[play_hand]` and `[discard]` and forcefully injecting indices from `BalatroAlgorithm.find_best_hand()`. This forces the AI to play trash hands instead of discarding. The new JSON planner must output exact indices (e.g., `{"action": "discard", "indices": [0, 4]}`), and the execution script **must actually use the planner's selected indices**, using the algorithm only as a "suggested hint" in the ephemeral context.
- **Shop Strictness (Anti-Hallucination):** The current prompt heavily coerces the LLM to "buy a +Mult Joker", causing it to falsely interpret Tarot/Planet cards as Jokers if no Jokers are present. The new planner must strictly parse shop items and their explicit types (`set`: Joker, Tarot, Planet from the game state payload). The system prompt must explicitly state: "Only buy an item if its type is exactly 'Joker'. Do not pretend a Tarot or Planet card is a Joker."

---

### **3. Evaluator Algorithm Upgrades**
Enrich pre-computed facts so the LLM receives highly strategic hints without reinventing the game's engine.

#### [MODIFY] [algorithms.py](file:///c:/Users/Josh/Documents/Project-Iris/skills/balatro_bot/modules/algorithms.py)
- **Heuristic Scorer:** Rely on heuristics rather than perfect simulators. Pass the LLM pre-computed heuristics such as "Base hand levels," "Active synergies tally," and "Flush probabilities," but leave complex joker cascade math to the LLM's generic reasoning.
- **Boss Blind Constraints:** Integrate boss debuff knowledge strictly during algorithm filtering before the LLM picks up the facts.
- **Extended Precomputes:** Supply facts for buy/reroll options, consumable targets, and bankroll thresholds.

---

### **4. Telemetry and Dashboard Tests**
Record granular analytics to trace algorithmic deaths and planner logic.

#### [MODIFY] [balatro_telemetry.py](file:///c:/Users/Josh/Documents/Project-Iris/skills/balatro_bot/modules/balatro_telemetry.py)
- **Decision-Aware Logging:** Expand telemetry away from simple end-of-game `log_death` recording. Log planner action, API result, reason category, boss, ante, joker layouts, consumables, and fallback trigger statistics.
- **Injectable Paths:** Inject filesystem paths for dashboards/tests so they do not break in sandcastled test environments.

#### [MODIFY] [test_balatro.py](file:///c:/Users/Josh/Documents/Project-Iris/tests/test_balatro.py) (and integration tests)
- **Fixture Tests:** Replace old 1-based test assertions with new Golden State JSON renders to lock the 0-based API parameters.
- **Planner Recovery Tests:** Test schema invalidity, fallback aborts, API rejects, and "always select" mechanics.

---

### **5. Main Orchestrator Lifecycle Cleanup**
Provide a singular start/stop/restart interface for all Iris mode switching.

#### [MODIFY] [wake_up.py](file:///c:/Users/Josh/Documents/Project-Iris/wake_up.py)
- **Centralized Lifecycle Contract:** Unify the duplicate run/lock checks for `start_micro_rpg` and `start_balatro`. 
- **CancellationToken Pattern:** Avoid forceful thread clearing. Implement `threading.Event()` kill-switches passed into the game loops to guarantee clean thread shutdowns, cleanly severed connections, and zero phantom API calls carrying over.

## Open Questions

None at this time. All architectural questions—including TTS latency masking, heuristic limits, testing protocols, model configuration splitting, and "always select" tracking—have been firmly answered in the plan.

## Verification Plan

### Automated Tests
- Completely delete and rewrite `test_balatro.py` and its legacy 1-based API assertions. Tests should be written from scratch to validate 0-based indexing and structural shapes relying **entirely on Golden JSON payloads** (mocked responses) captured from the game.
- Run `pytest tests/test_balatro_integration.py` to confirm the error backoff recovery mechanisms handle unparseable planner outputs gracefully.
- Run `pytest` on existing dashboard/telemetry components to ensure they accept injected paths.

### Manual Verification
- A local manual script restricting live smoke tests to user intervention. Engage "start_balatro" locally while the real Balatro game is hooked.
- Check the log tail to observe the silent `planner` calls hitting `http://127.0.0.1:12346/` and confirm JSON shape conformity.
- Validate that the TTS audio begins playing just prior to the in-game action executing to confirm latency is perfectly masked.
