---
trigger: always_on
---

- This project is 100% offline — never add cloud APIs or internet-dependent features
- All ollama.chat() calls must use options={'num_gpu': 15, 'temperature': 0.1}
- Never reference VRAM — the model runs on CPU/RAM with GPU layer offloading
- Correct term for system prompt behavior constraints is "explicit behavioral constraints" not "negative prompting"
- Python venv is at .venv/ — always use .venv\Scripts\python.exe
- MIC_DEVICE_INDEX = 1 (Realtek microphone)
- Audio outputs at 44100 Hz with silence padding
- Never use pyautogui for game input — use mGBA Python API instead (Pokemon project)
- Always add try/except with fallback behavior on hardware interactions
- Commit message format: "Add/Fix/Update [component] - [what changed]"