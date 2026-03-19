# Issues Log

## OmniSense Skill Creation
- Created `skills/omnisense_skill.py` to trigger the OmniSense feeder via HTTP GET request to `http://192.168.1.100/feed`.
- Implemented a try/except block with a 3-second timeout to prevent the main loop from freezing if the feeder is offline.
- Updated `wake_up.py` to detect if the transcribed text contains the words 'feed' or 'food'.
- Configured the workflow so if detected, it has the TTS say "Dispensing food now." and executes the OmniSense skill. Otherwise, it simply echoes the words back.

### [PENDING] OmniSense nudges saving as user messages in memory
Internal outcome nudges passed to chat_fn() are being logged as user messages.
Fix: add save=False flag to chat() for internal system calls.

### [RESOLVED] Idle Loop Firing trigger_feeder on Every Nudge
LLM was receiving tool definitions during idle nudges and defaulting to tool use.
Fixed by adding use_tools=False flag to _chat_internal() and passing it through
chat() → idle_loop() calls. Idle nudges now run with tools=[] so no hardware
tools can fire during unprompted speech.

### [RESOLVED] faster-whisper Transcribing Iris's Own TTS Output
Mic was picking up speaker audio creating a feedback loop where Iris talked to herself.
Short term fix: use headphones during testing.
Long term fix pending: add IRIS_SPEAKING mute flag to listen() in voice.py.