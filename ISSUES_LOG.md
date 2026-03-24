# Issues Log

## OmniSense Skill Creation
- Created `skills/omnisense_skill.py` to trigger the OmniSense feeder via HTTP GET request to `http://192.168.1.100/feed`.
- Implemented a try/except block with a 3-second timeout to prevent the main loop from freezing if the feeder is offline.
- Updated `wake_up.py` to detect if the transcribed text contains the words 'feed' or 'food'.
- Configured the workflow so if detected, it has the TTS say "Dispensing food now." and executes the OmniSense skill. Otherwise, it simply echoes the words back.

### [RESOLVED] Idle Loop Firing trigger_feeder on Every Nudge
LLM was receiving tool definitions during idle nudges and defaulting to tool use.
Fixed by adding use_tools=False flag to _chat_internal() and passing it through
chat() → idle_loop() calls. Idle nudges now run with tools=[] so no hardware
tools can fire during unprompted speech.

### [RESOLVED] faster-whisper Transcribing Iris's Own TTS Output
Mic was picking up speaker audio creating a feedback loop where Iris talked to herself.
Fixed: Added is_speaking flag to block listening while TTS is playing.

---

## Plan V3: Deterministic Booth Assistant (Arduin-o-vation)

### [RESOLVED] Speech Hijacking - Sentences Overlapping
**Problem:** When the LLM generated multiple sentences rapidly, each new sentence would hijack the audio device, cutting off the previous sentence mid-speech.
**Solution:** Implemented a speech queue (`queue.Queue()`) with a dedicated worker thread (`_speech_worker`) that processes sentences sequentially. Each sentence now completes before the next begins.
- Files: `core/voice.py`

### [RESOLVED] Emoji Reading - TTS Speaking Emoji Characters
**Problem:** Neural TTS (Kokoro) would literally read emoji characters like "sparkles" or "folded hands" instead of ignoring them.
**Solution:** Added comprehensive emoji stripper in `strip_markdown()` using targeted Unicode regex ranges to remove emojis while preserving accented characters.
- Files: `core/iris_agent.py`

### [RESOLVED] Avatar Stuck in Speaking State
**Problem:** After TTS finished or was interrupted, the avatar would remain stuck in "speaking" state instead of returning to "idle".
**Solution:** Added unconditional `avatar.set_state("idle")` after `sd.wait()` in `_execute_tts()` with a check for "surprised" state to preserve interruption reactions.
- Files: `core/voice.py`

### [RESOLVED] Pre-warm Blocking GUI Startup
**Problem:** LLM pre-warm was running synchronously, blocking the GUI from starting for 5+ seconds.
**Solution:** Moved pre-warm to a background daemon thread with a brief delay before launching the main loop.
- Files: `wake_up.py`

### [RESOLVED] Duplicate speak() Methods
**Problem:** There were two `speak()` methods in voice.py - the old blocking version and the new queue-based version. Python only kept the second one, so the queue was never used.
**Solution:** Removed the duplicate old `speak()` method, consolidated all TTS logic into `_execute_tts()`.
- Files: `core/voice.py`

### [RESOLVED] is_interruptible Not Set in Queue TTS
**Problem:** The queue-based TTS (`_execute_tts`) wasn't setting `is_interruptible.set()`, so normal responses couldn't be interrupted - only idle nudges were interruptible.
**Solution:** Added `self.is_interruptible.set()` before playback and `clear()` after in `_execute_tts`.
- Files: `core/voice.py`

### [RESOLVED] Idle Nudges While Conversation Ongoing
**Problem:** Idle nudges would trigger even when there were still pending sentences in the queue or during active conversation.
**Solution:** Added queue emptiness check (`speech_queue.empty()`) and increased timeout from 15 to 20 seconds in idle_loop.
- Files: `core/iris_agent.py`

### [RESOLVED] Ghost Responses - Old Speech Playing After New Input
**Problem:** When user typed a new message, old queued sentences would continue playing after the new response started, creating "ghost" conversation overlap.
**Solution:** 
1. Added `flush()` method to VoiceManager that clears queue and stops current audio
2. Called `flush()` at the start of `handle_input()` in wake_up.py
3. Added queue check to idle loop to prevent nudges during active conversation
- Files: `core/voice.py`, `wake_up.py`, `core/iris_agent.py`

### [RESOLVED] Float Errors with SCALE_FACTOR 1.5
**Problem:** With SCALE_FACTOR = 1.5, all calculations produced floats (e.g., 15.0) instead of integers. tkinter and PIL require integers for dimensions and font sizes.
**Solution:** Wrapped all SCALE_FACTOR calculations in `int()` - AVATAR_SIZE, EMOJI_SIZE, all font sizes, wraplength, and padding values.
- Files: `core/avatar.py`

### [RESOLVED] Missing float_emoji() Method
**Problem:** Quiz skill called `self.avatar.float_emoji()` but the method didn't exist in AvatarWindow, causing AttributeError crashes during quizzes.
**Solution:** Implemented simple `float_emoji()` method that displays an emoji briefly (2 seconds) then removes it.
- Files: `core/avatar.py`

### [RESOLVED] Context Switching Code Still Present After Removal
**Problem:** Removed `set_context()` method from Iris class but references remained in wake_up.py and test_quiz.py, causing errors.
**Solution:** Deleted all references to `set_context` in wake_up.py (mode switching blocks and initial call) and test_quiz.py.
- Files: `wake_up.py`, `test_quiz.py`

### [RESOLVED] Seamless Tools - Context-Based Tool Switching
**Problem:** Originally required saying "feed mode" to activate feeder tool - clunky for exhibit visitors.
**Solution:** Replaced TOOL_CONTEXTS dictionary with flat AVAILABLE_TOOLS list, giving LLM permanent access to all tools and letting it decide when to use them based on conversation context.
- Files: `core/iris_agent.py`

### [RESOLVED] Avatar Scaling - 1.5x with Proper Fonts
**Problem:** Avatar was either too small (1x) or too large (2x), fonts were too small, and window was fully opaque.
**Solution:** Set SCALE_FACTOR = 1.5, changed font to Segoe UI (Windows-native), increased all font sizes proportionally, removed alpha transparency (window uses transparentcolor for transparency effect).
- Files: `core/avatar.py`

---

## Configuration (Environment Variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MIC_INDEX` | None (OS default) | Override if Windows reassigns audio device |
| `AEC_MULTIPLIER` | 2.0 | Tune threshold for ambient noise |