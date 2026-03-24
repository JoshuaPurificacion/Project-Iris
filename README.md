# 🌸 Project Iris

> *"An experiment that got a little out of hand. The fun kind, though."*

Project Iris is a fully offline, locally-run AI VTuber built for a university engineering exhibit. She listens, speaks, remembers, reacts, and can trigger real hardware — all without an internet connection.

**Built by:** Joshua Purificacion  
**University:** University of the East — Manila  
**Course:** Computer Engineering — NCP2202 / NCP2204  
**Debut:** March 19, 2026

---

## 🎯 What Iris Can Do

- 🎤 **Listen** — Voice Activity Detection using RMS amplitude, no fixed recording windows
- 🗣️ **Speak** — Kokoro-82M neural TTS with custom pitch tuning (af_sky, +5 steps)
- 🧠 **Think** — Qwen2.5 7B via Ollama, streaming sentence-by-sentence responses
- 💾 **Remember** — Persistent conversation history via sliding window memory
- 💬 **Chat** — Both voice and text input supported simultaneously
- 😴 **Idle** — Speaks unprompted during silence with casual observations and humor
- 🐾 **Trigger Hardware** — Controls OmniSense ESP32-CAM pet feeder via HTTP
- 📝 **Quiz** — Tests visitors on Computer Engineering topics with score tracking
- 🎭 **Express** — Animated PNG avatar with state-based expressions and animations

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                   wake_up.py                         │
│          Main orchestration loop                     │
└──────┬──────────────────────────────┬───────────────┘
       │                              │
┌──────▼──────┐                ┌──────▼──────┐
│  VoiceManager│                │ AvatarWindow │
│  core/voice  │                │ core/avatar  │
│              │                │              │
│ • RMS VAD    │                │ • tkinter    │
│ • Whisper STT│                │ • 4 states   │
│ • Kokoro TTS │                │ • animations │
│ • Pitch shift│                │ • captions   │
│ • Resample   │                │ • text input │
└──────┬───────┘                └──────────────┘
       │
┌──────▼───────┐
│  IrisAgent   │
│core/iris_agent│
│              │
│ • Qwen2.5 7B │
│ • Streaming  │
│ • Tool calls │
│ • Idle loop  │
└──────┬───────┘
       │
┌──────▼───────┐     ┌──────────────────┐
│ MemoryManager│     │     Skills        │
│core/memory   │     │                  │
│              │     │ omnisense_skill  │
│ • history.json     │ quiz_skill       │
│ • Sliding    │     │                  │
│   window 10x │     └──────────────────┘
└──────────────┘
```

---

## 🔧 Tech Stack

| Component | Technology |
|---|---|
| LLM | Qwen2.5 7B via Ollama (CPU + GPU offload) |
| Speech-to-Text | faster-whisper base (CPU) |
| Text-to-Speech | Kokoro-82M ONNX (CPU) |
| Voice Activity Detection | Custom RMS amplitude detection |
| Avatar Display | tkinter + Pillow |
| Memory | JSON sliding window (10 exchanges) |
| Hardware Control | HTTP GET to ESP32-CAM (mDNS) |
| Audio | sounddevice + scipy resampling |

---

## 💻 Hardware Requirements

- **Laptop:** Lenovo LOQ (or equivalent)
- **GPU:** NVIDIA RTX 4050 (6GB VRAM) — 15 layers offloaded
- **RAM:** 16GB recommended (runs at ~60% usage)
- **OS:** Windows 11
- **Hardware:** ESP32-CAM OmniSense pet feeder (optional)
- **Network:** Phone hotspot (OmniSense connects to this)

---

## 🚀 Setup

**1. Clone the repo**
```bash
git clone https://github.com/JoshuaPurificacion/Project-Iris.git
cd Project-Iris
```

**2. Create virtual environment**
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**3. Download models**

Ollama (LLM):
```bash
ollama pull qwen2.5
```

Kokoro TTS model files:
```bash
python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='hexgrad/Kokoro-82M', filename='kokoro-v0_19.onnx', local_dir='models/tts')"
```

Download `voices.bin` from: `https://github.com/thewh1teagle/kokoro-onnx/releases`  
Place in `models/tts/voices.bin`

**4. Set environment variable (for GPU)**
```bash
setx CUDA_VISIBLE_DEVICES "0"
```

**5. Run Iris**
```bash
.venv\Scripts\python.exe wake_up.py
```

Or use the launcher script:
```bash
start_booth.bat
```

---

## 🎮 How to Interact

| Action | How |
|---|---|
| Talk to Iris | Speak into microphone — VAD detects speech automatically |
| Type to Iris | Use the text input box on the avatar window |
| Feed the cat | Say or type "feed the cat" or "demo feed" |
| Start a quiz | Say or type "quiz me" |
| Stop a quiz | Say or type "stop quiz" |
| Move avatar | Hold click 2 seconds then drag |
| Bob avatar | Single click |

---

## ⚙️ Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MIC_INDEX` | None (OS default) | Override if Windows reassigns audio device |
| `AEC_MULTIPLIER` | 2.0 | Tune threshold for echo cancellation (higher = less sensitive) |

Set via PowerShell before running:
```powershell
$env:MIC_INDEX = "1"
$env:AEC_MULTIPLIER = "2.5"
```

---

## 📁 Project Structure

```
Project-Iris/
├── core/
│   ├── iris_agent.py      # LLM brain + tool calling + idle loop
│   ├── voice.py           # STT + TTS + VAD + speech queue
│   ├── avatar.py          # tkinter display + animations
│   └── logger.py          # Conversation logging
├── skills/
│   ├── omnisense_skill.py # ESP32-CAM pet feeder trigger
│   └── quiz_skill.py      # CE quiz with score tracking
├── assets/
│   └── avatar/            # Iris PNG expressions
├── models/
│   └── tts/               # Kokoro model files (not in repo)
├── logs/                  # Auto-generated conversation logs
├── wake_up.py             # Main entry point
├── start_booth.bat        # Windows launcher script
├── requirements.txt
├── ISSUES_LOG.md
└── README.md
```

---

## 🧪 Key Engineering Decisions

**Why fully offline?**  
University exhibit halls have unreliable WiFi. Every cloud dependency is a potential demo failure. All inference runs locally.

**Why Qwen2.5 over larger models?**  
Better structured output and conversational quality at the same size class. RAM-efficient with GPU layer offloading.

**Why RMS VAD over webrtcvad?**  
webrtcvad is a 2016 C++ library incompatible with Python 3.12. Custom numpy RMS detection is more reliable, zero external dependencies, and tunable to the specific microphone environment.

**Why streaming LLM responses?**  
Reduces perceived latency from 10-15 seconds to 2-3 seconds. Iris speaks each sentence as it arrives rather than waiting for the full response.

**Why tkinter over VTube Studio?**  
Direct Python integration — avatar state changes are function calls, not audio-triggered heuristics. More reliable and fully controllable.

---

## 🔮 Future Roadmap

- [ ] Fine-tuning on exhibit conversation data via Unsloth + LoRA
- [ ] Filipino culture RAG (yt-dlp + deep-translator pipeline)
- [ ] Twitch chat integration
- [ ] Vision system — screen reading via Moondream2
- [ ] Pokemon FireRed autonomous player (separate repo: Project-Iris-Pokemon)

---

## 📋 Issues Log

See [ISSUES_LOG.md](ISSUES_LOG.md) for a full history of bugs encountered and resolved during development.

---

## 🙏 Credits

- **Kokoro-82M TTS** — hexgrad / thewh1teagle
- **faster-whisper** — SYSTRAN
- **Ollama** — Ollama team
- **Iris avatar design** — AI generated, red/white chibi style
- **Project AIRI** — moeru-ai (conceptual inspiration)
- **Neuro-sama** — Vedal987 (inspiration)
