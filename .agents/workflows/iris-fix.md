---
description: /iris-fix Use when debugging a crash or unexpected behavior
---

Project Iris bug report:

File: [FILE]
Error/Behavior: [PASTE ERROR OR DESCRIBE BEHAVIOR]

Constraints that must not change:
- num_gpu: 15, temperature: 0.1 on all ollama.chat() calls
- MIC_DEVICE_INDEX = 1
- Audio at 44100 Hz with silence padding
- No cloud APIs

Find the root cause and fix it. Show the changed lines only.