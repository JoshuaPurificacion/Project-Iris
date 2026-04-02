# Project Iris - Development Conversation Log

This document contains the full development history and decisions made during the creation of Project Iris for the Arduin-o-vation university exhibit.

For Balatro-specific implementation notes and test history, see [docs/BALATRO_DEVELOPMENT_LOG.md](docs/BALATRO_DEVELOPMENT_LOG.md).

---

## Session Date: March 25, 2026

### Developer: Joshua Purificacion (with AI Architect Assistant)

### Project: Project Iris v3 - Deterministic Booth Assistant with Interruptible Voice

---

## Key Decisions & Implementation Summary

### 1. Plan V3 - The "Ghost Buster" Update

**Problem Identified:**
- Speech hijacking: Sentences overlapping because each new sentence would hijack the audio device
- Ghost responses: Old queued sentences playing after new user input arrived
- Idle nudges triggering during active conversation
- Emojis being read as "sparkles" by TTS

**Solution Implemented:**
- Speech queue with sequential processing (`queue.Queue()`)
- Flush mechanism that clears queue + stops audio on new input
- Queue emptiness check in idle loop (waits 20 seconds after last speech)
- Comprehensive emoji stripping with targeted Unicode regex

### 2. UI Updates

**Avatar Scaling:**
- Changed from 2x to 1.5x scale factor
- Changed font from Arial to Segoe UI (Windows-native)
- Window transparency via transparentcolor (not -alpha which made everything translucent)
- Added float_emoji() method for quiz feedback

### 3. Architecture Changes

**Seamless Tools:**
- Replaced TOOL_CONTEXTS dictionary with flat AVAILABLE_TOOLS list
- Removed set_context() method entirely
- LLM now decides when to use tools based on conversation context

**Pre-warm:**
- Moved LLM initialization to background thread
- Prevents GUI blocking on startup

### 4. Bug Fixes

| Bug | Fix |
|-----|-----|
| Float errors with SCALE_FACTOR 1.5 | Wrapped all calculations in int() |
| Missing float_emoji() method | Implemented in avatar.py |
| Duplicate speak() methods | Removed old blocking version |
| is_interruptible not set in queue TTS | Added set() before playback |
| Queue not cleared on new input | Added flush() method and call in handle_input() |

### 5. Files Modified

- `core/voice.py` - Speech queue, flush(), selective interruption, TTS with logging
- `core/iris_agent.py` - Queue check in idle loop, seamless tools, personality updates
- `core/avatar.py` - 1.5x scale, Segoe UI, float_emoji, proper integer handling
- `core/logger.py` - New logging module for conversations
- `wake_up.py` - flush on input, background pre-warm
- `ISSUES_LOG.md` - Complete issues history
- `README.md` - Updated setup and features

### 6. Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| MIC_INDEX | None (OS default) | Override audio device |
| AEC_MULTIPLIER | 2.0 | Echo cancellation sensitivity |

---

## Commands to Run

```powershell
# PowerShell
.\start_booth.ps1

# Or Command Prompt
start_booth.bat
```

---

## Key Features

✅ Speech queue with sequential processing  
✅ Flush on new user input (no ghost responses)  
✅ Queue-aware idle detection  
✅ Emoji stripping for clean TTS  
✅ 1.5x scaled avatar with Segoe UI font  
✅ Transparent window with solid text  
✅ Seamless tools (no "feed mode" needed)  
✅ Background LLM pre-warm  
✅ Conversation logging  

---

## Git Commit

```
Final Project Iris v3 - Arduin-o-vation exhibit ready

14 files changed, 1949 insertions(+), 376 deletions(-)
```

---

## Notes

This was a collaborative development session between Joshua Purificacion (Computer Engineering student, University of the East - Manila) and an AI Architect assistant. The goal was to create a robust, reliable AI booth assistant for a live university exhibit that could handle interruptions, queue management, and provide an engaging experience for visitors.

The final system is fully offline-capable, uses deterministic texture analysis instead of probabilistic AI, and features interruptible voice capabilities.

---

*End of Development Session*
*Date: March 25, 2026*
*Project: OmniSense / Project Iris*
*Exhibit: Arduin-o-vation - University of the East, Manila*

---

## Session Date: April 2, 2026

### Focus: Balatro Shop Reliability + Joker Synergy Evaluation

### Key Outcomes

1. Added explicit planner-first shop reasoning via a new `synergy_evaluation` field in the Balatro planner output schema.
2. Updated shop directives to require comparing candidate Joker `value.effect` text against currently owned Joker effects before selecting `action`.
3. Added strict shop availability constraints so planner avoids invalid actions when shop, pack, or voucher inventories are empty or reroll is unaffordable.
4. Improved consumable compatibility by supporting both payload spellings: `consumeables` and `consumables`.
5. Added a reproducible state inspection utility to capture owned and shop jokers into a txt snapshot for debugging and prompt validation.

### Tests Added/Validated

- `tests/test_balatro_router_prompt.py`
	- Verifies unavailable shop actions are blocked.
	- Verifies `synergy_evaluation` requirement appears in shop guidance.
	- Verifies planner prompt places `synergy_evaluation` before `action`.
	- Verifies real Joker `value.effect` text appears in planner context.

- `tests/test_balatro_session.py`
	- Verifies `PlannerOutput` accepts `synergy_evaluation`.
	- Verifies backward compatibility when `synergy_evaluation` is omitted.

- `tests/test_balatro_algorithms.py`
	- Verifies consumable evaluation stability with empty input.
	- Verifies target selection logic for enhancer/destructor consumables.

### Files Updated This Session

- `skills/balatro/session.py`
- `skills/balatro/router.py`
- `skills/balatro/prompt_builder.py`
- `skills/balatro/actions/utility.py`
- `skills/balatro_bot/modules/algorithms.py`
- `tests/test_balatro_router_prompt.py`
- `tests/test_balatro_session.py`
- `tests/test_balatro_algorithms.py`
- `scripts/inspect_jokers.py`
- `scripts/test_api_jokers.txt`
- `ISSUES_LOG.md`
- `DEVELOPMENT_LOG.md`