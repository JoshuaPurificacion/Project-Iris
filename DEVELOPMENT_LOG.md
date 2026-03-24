# Project Iris - Development Conversation Log

This document contains the full development history and decisions made during the creation of Project Iris for the Arduin-o-vation university exhibit.

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