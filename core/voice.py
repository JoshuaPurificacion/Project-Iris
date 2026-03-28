import sounddevice as sd
import numpy as np
import librosa
import scipy.signal
from faster_whisper import WhisperModel
import pyttsx3
from kokoro_onnx import Kokoro
import os
import threading
import queue
import re
import json
from pathlib import Path
from core.logger import log_voice_input, log_caption, log_system

AEC_MULTIPLIER = float(os.getenv("AEC_MULTIPLIER", "2.0"))

VOICE_PITCH_STEPS = 5
KOKORO_MODEL_PATH = Path("models/tts/kokoro-v0_19.onnx")
KOKORO_JSON_PATH = Path("models/tts/voices.json")
KOKORO_NPZ_PATH = Path("models/tts/voices.npz")


def _get_best_providers():
    import onnxruntime as ort

    available = ort.get_available_providers()
    preferred = [
        "OpenVINOExecutionProvider",
        "ROCMExecutionProvider",
        "CPUExecutionProvider",
    ]
    selected = [p for p in preferred if p in available]
    print(f"[Kokoro] Using providers: {selected}")
    return selected


class VoiceManager:
    def __init__(self):
        self.is_speaking = threading.Event()
        self.is_interruptible = threading.Event()
        self.speech_queue = queue.Queue()
        self.out_stream = None
        self.abort_flag = threading.Event()
        self.kokoro_error = None

        voices_path = self._ensure_kokoro_voices_npz()

        # Initialize the whisper model for local inference.
        # This will load the pre-downloaded 'base' model without relying on cloud APIs.
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

        # Initialize Kokoro TTS model
        try:
            self.kokoro = Kokoro(
                str(KOKORO_MODEL_PATH),
                str(voices_path),
            )
        except Exception as e:
            print(f"[VoiceManager] Failed to load Kokoro: {e}")
            self.kokoro_error = str(e)
            self.kokoro = None

        # Start the background worker that processes the speech queue
        threading.Thread(target=self._speech_worker, daemon=True).start()

    # VAD configuration (RMS-based)
    SPEAKING_THRESHOLD = 0.09  # RMS volume threshold — raise if too sensitive
    SILENCE_FRAMES = 20  # consecutive silent frames before stopping
    MAX_RECORD_SECONDS = 15  # safety cap
    FRAME_DURATION = 0.03  # 30ms frames
    SAMPLE_RATE = 16000
    FRAME_SIZE = int(16000 * 0.03)  # = 480 samples

    def _speech_worker(self):
        """Processes the speech queue sequentially with zero thread overlap."""
        while True:
            text, avatar = self.speech_queue.get()
            log_system(f"Processing: {text[:40]}...")
            self._execute_tts(text, avatar)
            self.speech_queue.task_done()

    def speak(self, text, avatar=None):
        """Adds text to the queue and logs the entry."""
        log_system(f"Enqueued: {text[:40]}...")
        self.speech_queue.put((text, avatar))

    def flush(self):
        """Stop all audio and clear pending speech queue."""
        with self.speech_queue.mutex:
            self.speech_queue.queue.clear()
        # --- SAFE INTERRUPTION ---
        if hasattr(self, "out_stream") and self.out_stream is not None:
            try:
                self.out_stream.abort()
            except Exception:
                pass
        self.abort_flag.set()
        self.is_speaking.clear()
        self.is_interruptible.clear()
        log_system("🔊 Audio and queue flushed.")

    def interrupt_playback(self, avatar=None, reason="user_interrupt"):
        """Unified interrupt: stop audio, clear queue, and signal abort to generation."""
        with self.speech_queue.mutex:
            self.speech_queue.queue.clear()

        if hasattr(self, "out_stream") and self.out_stream is not None:
            try:
                self.out_stream.abort()
            except Exception:
                pass

        self.abort_flag.set()
        self.is_speaking.clear()
        self.is_interruptible.clear()

        if avatar is not None:
            avatar.set_state("surprised")
            try:
                avatar.show_caption("Pausing. I'm listening.")
            except Exception:
                pass

        log_system(f"🔇 Playback interrupted ({reason}).")

    def _execute_tts(self, text, avatar=None):
        """Blocking TTS execution with sequential state cleanup."""
        if not text.strip():
            return
        log_caption(text)
        if avatar:
            avatar.show_caption(text)

        # Claim speaking state immediately so idle loop knows generation is in-flight
        self.is_speaking.set()
        self.is_interruptible.set()

        try:
            if self.kokoro is None:
                raise RuntimeError(
                    f"Kokoro unavailable: {self.kokoro_error or 'not loaded'}"
                )

            log_system("TTS: Using Kokoro")
            # Prepare Audio
            audio, sr = self.kokoro.create(
                text, voice="af_sky", speed=0.9, lang="en-us"
            )
            audio = librosa.effects.pitch_shift(
                audio.astype(float), sr=sr, n_steps=VOICE_PITCH_STEPS
            )
            audio = scipy.signal.resample_poly(audio, 44100, 24000).astype("float32")

            silence_pad = np.zeros(int(44100 * 0.3), dtype="float32")
            audio = np.concatenate([audio, silence_pad])

            # User may have interrupted during generation
            if self.abort_flag.is_set():
                self.is_speaking.clear()
                self.is_interruptible.clear()
                return

            # Update State & Play
            if avatar:
                avatar.set_state("speaking")

            # --- ISOLATED PLAYBACK STREAM ---
            audio_data = audio
            if audio_data.ndim == 1:
                audio_data = audio_data.reshape(-1, 1)

            try:
                self.out_stream = sd.OutputStream(
                    samplerate=44100, channels=1, dtype="float32"
                )
                self.out_stream.start()
                self.out_stream.write(audio_data)
                self.out_stream.stop()
                self.out_stream.close()
            except sd.PortAudioError:
                pass  # Safely catches the abort() command if interrupted!
            finally:
                self.out_stream = None

            # Sequential Cleanup (Only runs AFTER audio finishes)
            self.is_speaking.clear()
            self.is_interruptible.clear()
            if avatar:
                if avatar.current_state != "surprised":
                    avatar.set_state("idle")
                # Only hide prototype if no more sentences are queued (thread-safe)
                if self.speech_queue.empty() and hasattr(avatar, "hide_prototype"):
                    avatar.hide_prototype()

        except Exception as e:
            log_system(f"TTS: Kokoro failed ({e}), using pyttsx3 fallback")
            log_system(
                "[TTS WARNING] Walkie-Talkie mode active: pyttsx3 voice interruptions disabled."
            )
            try:
                if avatar:
                    avatar.set_state("speaking")
                self.is_speaking.set()
                self.is_interruptible.set()

                engine = pyttsx3.init()
                engine.say(text)
                engine.runAndWait()

                self.is_speaking.clear()
                self.is_interruptible.clear()
                if avatar:
                    if avatar.current_state != "surprised":
                        avatar.set_state("idle")
                    # Only hide prototype if no more sentences are queued (thread-safe)
                    if self.speech_queue.empty() and hasattr(avatar, "hide_prototype"):
                        avatar.hide_prototype()

            except Exception as e2:
                log_system(f"TTS: pyttsx3 also failed: {e2}")
                self.is_speaking.clear()
                self.is_interruptible.clear()
                if avatar:
                    avatar.set_state("idle")
                    # Only hide prototype if no more sentences are queued (thread-safe)
                    if self.speech_queue.empty() and hasattr(avatar, "hide_prototype"):
                        avatar.hide_prototype()
                print(f"[TTS FALLBACK] {text}")

    def listen(self, avatar=None):
        """
        Record audio using a simple RMS volume detection threshold.
        Continuously listens and allows for TTS interruption.
        """
        if avatar is not None:
            avatar.show_listening()

        frames = []
        silent_frames = 0
        speaking_started = False
        pre_buffer = []

        print("[VoiceManager] Listening...")
        num_frames = int(self.MAX_RECORD_SECONDS / self.FRAME_DURATION)

        target_mic = os.getenv("MIC_INDEX")
        device_id = int(target_mic) if target_mic is not None else None

        with sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=self.FRAME_SIZE,
            device=device_id,
        ) as stream:
            for _ in range(num_frames):
                frame, _ = stream.read(self.FRAME_SIZE)
                volume = np.sqrt(np.mean(np.square(frame)))

                # --- Selective Interruption ---
                if self.is_speaking.is_set():
                    # Allow soft preemption while Iris is speaking
                    if volume > (self.SPEAKING_THRESHOLD * AEC_MULTIPLIER * 0.7):
                        print("[VoiceManager] Interrupting TTS — user started talking.")
                        self.interrupt_playback(avatar=avatar, reason="voice_interrupt")

                        speaking_started = True
                        frames = [frame.copy()]
                        pre_buffer.clear()
                        silent_frames = 0
                        continue
                    else:
                        # Act completely deaf while answering a real question
                        continue

                # --- Normal Listening ---
                is_speech = volume > self.SPEAKING_THRESHOLD
                if not speaking_started:
                    pre_buffer.append(frame.copy())
                    if len(pre_buffer) > 10:
                        pre_buffer.pop(0)
                    if is_speech:
                        speaking_started = True
                        frames.extend(pre_buffer)
                        pre_buffer.clear()
                        print("[VoiceManager] Speech detected, recording...")
                else:
                    frames.append(frame.copy())
                    silent_frames = 0 if is_speech else silent_frames + 1
                    if silent_frames > self.SILENCE_FRAMES:
                        break

        if not frames or not speaking_started:
            if avatar is not None:
                avatar.show_idle()
                avatar.set_state("idle")
            return ""

        # Concatenate float32 frames for faster-whisper
        audio = np.concatenate(frames).flatten()
        print("[VoiceManager] Transcribing locally...")
        segments, _ = self.model.transcribe(audio, language="en")
        result = " ".join(s.text for s in segments).strip()
        if result:
            log_voice_input(result)

        if avatar is not None:
            avatar.show_idle()
            avatar.set_state("idle")

        return result

    def _ensure_kokoro_voices_npz(self) -> Path:
        """Ensure Kokoro voices are in NPZ format expected by kokoro-onnx."""
        if KOKORO_NPZ_PATH.exists():
            return KOKORO_NPZ_PATH

        if not KOKORO_JSON_PATH.exists():
            self.kokoro_error = "voices.json missing"
            return KOKORO_JSON_PATH

        try:
            data = json.loads(KOKORO_JSON_PATH.read_text(encoding="utf-8"))
            arrays = {k: np.array(v, dtype=np.float32) for k, v in data.items()}
            np.savez_compressed(KOKORO_NPZ_PATH, **arrays)
            log_system("[Kokoro] Converted voices.json to voices.npz")
            return KOKORO_NPZ_PATH
        except Exception as e:
            self.kokoro_error = f"voices conversion failed: {e}"
            return KOKORO_JSON_PATH
