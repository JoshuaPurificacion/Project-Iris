# Issues Log

## OmniSense Skill Creation
- Created `skills/omnisense_skill.py` to trigger the OmniSense feeder via HTTP GET request to `http://192.168.1.100/feed`.
- Implemented a try/except block with a 3-second timeout to prevent the main loop from freezing if the feeder is offline.
- Updated `wake_up.py` to detect if the transcribed text contains the words 'feed' or 'food'.
- Configured the workflow so if detected, it has the TTS say "Dispensing food now." and executes the OmniSense skill. Otherwise, it simply echoes the words back.

### [PENDING] OmniSense nudges saving as user messages in memory
Internal outcome nudges passed to chat_fn() are being logged as user messages.
Fix: add save=False flag to chat() for internal system calls.
