import sounddevice as sd
import numpy as np
import librosa
import collections
import webrtcvad
from faster_whisper import WhisperModel
import pyttsx3
from kokoro_onnx import Kokoro

VOICE_PITCH_STEPS = 4
CABLE_DEVICE_INDEX = 6
IRIS_SPEAKING = False

class VoiceManager:
    def __init__(self):
        # Initialize the whisper model for local inference.
        # This will load the pre-downloaded 'base' model without relying on cloud APIs.
        self.model = WhisperModel("base", device="cpu", compute_type="int8")
        
        # Initialize Kokoro TTS model
        try:
            self.kokoro = Kokoro("models/tts/kokoro-v0_19.onnx", "models/tts/voices.bin")
        except Exception as e:
            print(f"[VoiceManager] Failed to load Kokoro: {e}")
            self.kokoro = None

    # VAD configuration
    VAD_AGGRESSIVENESS = 2      # 0-3, higher = more aggressive silence detection
    SAMPLE_RATE       = 16000
    FRAME_DURATION    = 30      # ms per frame (10, 20, or 30 only)
    FRAME_SIZE        = int(16000 * 30 / 1000)  # = 480 samples
    SILENCE_FRAMES    = 30      # consecutive silent frames before stopping
    MAX_RECORD_SECONDS = 15     # safety cap

    def listen(self):
        """
        Record audio using Voice Activity Detection (VAD) via webrtcvad.
        Starts capturing when speech is detected via a ring-buffer vote,
        stops when SILENCE_FRAMES consecutive silent frames are observed.
        Returns the transcribed text string, or '' if nothing was spoken.
        """
        if IRIS_SPEAKING:
            return ""

        vad = webrtcvad.Vad(self.VAD_AGGRESSIVENESS)
        frames = []
        silent_frames = 0
        speaking_started = False
        ring_buffer = collections.deque(maxlen=10)

        print("[VoiceManager] Listening...")

        max_iterations = int(self.SAMPLE_RATE / self.FRAME_SIZE * self.MAX_RECORD_SECONDS)

        with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1,
                            dtype='int16', blocksize=self.FRAME_SIZE) as stream:
            for _ in range(max_iterations):
                if IRIS_SPEAKING:
                    break

                frame, _ = stream.read(self.FRAME_SIZE)
                frame_bytes = frame.tobytes()
                is_speech = vad.is_speech(frame_bytes, self.SAMPLE_RATE)

                if not speaking_started:
                    ring_buffer.append((frame_bytes, is_speech))
                    num_voiced = len([f for f, s in ring_buffer if s])
                    if num_voiced > 0.7 * ring_buffer.maxlen:
                        speaking_started = True
                        frames.extend([f for f, _ in ring_buffer])
                        ring_buffer.clear()
                        print("[VoiceManager] Speech detected, recording...")
                else:
                    frames.append(frame_bytes)
                    if not is_speech:
                        silent_frames += 1
                    else:
                        silent_frames = 0
                    if silent_frames > self.SILENCE_FRAMES:
                        print("[VoiceManager] Silence detected, stopping.")
                        break

        if not frames or not speaking_started:
            return ""

        # Convert raw int16 bytes → float32 array for faster-whisper
        audio = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        print("[VoiceManager] Transcribing locally...")
        segments, _ = self.model.transcribe(audio, language="en")
        return " ".join(s.text for s in segments).strip()

    def speak(self, text, avatar=None):
        """
        Read the given text aloud using Kokoro-82M locally.
        Catches any errors silently and falls back to pyttsx3.
        avatar: optional AvatarWindow instance used to reflect speaking state.
        """
        global IRIS_SPEAKING
        try:
            if not self.kokoro:
                raise ValueError("Kokoro model not loaded")

            # Generate audio using Kokoro
            audio, sr = self.kokoro.create(text, voice="af_sky", speed=1.0, lang="en-us")

            audio = librosa.effects.pitch_shift(audio.astype(float), sr=sr, n_steps=VOICE_PITCH_STEPS)
            audio = audio.astype('float32')

            # Signal avatar before playback
            if avatar is not None:
                avatar.set_state("speaking")

            # Play via sounddevice at 24000 Hz as specified
            IRIS_SPEAKING = True
            try:
                sd.play(audio, samplerate=24000, device=CABLE_DEVICE_INDEX)
                sd.wait()
            finally:
                IRIS_SPEAKING = False

            # Return avatar to idle after playback
            if avatar is not None:
                avatar.set_state("idle")
        except Exception as e:
            print(f"[TTS FALLBACK - pyttsx3]")
            try:
                if avatar is not None:
                    avatar.set_state("speaking")
                IRIS_SPEAKING = True
                try:
                    engine = pyttsx3.init()
                    engine.say(text)
                    engine.runAndWait()
                finally:
                    IRIS_SPEAKING = False
                if avatar is not None:
                    avatar.set_state("idle")
            except Exception:
                IRIS_SPEAKING = False
                # Fallback to console print on TTS failure
                if avatar is not None:
                    avatar.set_state("idle")
                print(f"[TTS FALLBACK] {text}")
