import sounddevice as sd
import numpy as np
import librosa
from faster_whisper import WhisperModel
import pyttsx3
from kokoro_onnx import Kokoro

VOICE_PITCH_STEPS = 3

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

    # Minimum RMS energy level to consider audio as speech (not silence/noise)
    SILENCE_THRESHOLD = 0.01

    def listen(self):
        """
        Record a 5-second audio chunk from the microphone using sounddevice,
        then transcribe it using the faster-whisper base model.
        Returns the transcribed text string, or an empty string if silence is detected.
        """
        fs = 16000  # Default sample rate required by Whisper
        duration = 5  # seconds

        print("[VoiceManager] Listening for 5 seconds...")
        # Record from default microphone
        recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='float32')
        sd.wait()  # Block execution until recording finishes

        # Flatten the 2D array into a 1D array for transcription
        audio_data = np.squeeze(recording)

        # --- Silence gate: skip Whisper entirely if audio is too quiet ---
        mean_amp = np.mean(np.abs(audio_data))
        if mean_amp < 0.01:
            print("[VoiceManager] Silence detected, skipping.")
            return ""

        print("[VoiceManager] Transcribing locally...")
        # Force English to prevent Whisper hallucinating in other languages on noise
        segments, info = self.model.transcribe(audio_data, beam_size=5, language="en")

        # Combine transcribed segments into a single string
        text = " ".join([segment.text for segment in segments]).strip()

        # Filter out ellipsis-only hallucinations (e.g. "...  ...  ...")
        if all(c in ". " for c in text):
            return ""

        return text

    def speak(self, text):
        """
        Read the given text aloud using Kokoro-82M locally.
        Catches any errors silently and falls back to pyttsx3.
        """
        try:
            if not self.kokoro:
                raise ValueError("Kokoro model not loaded")
                
            # Generate audio using Kokoro
            audio, sr = self.kokoro.create(text, voice="af_sky", speed=1.0, lang="en-us")
            
            audio = librosa.effects.pitch_shift(audio.astype(float), sr=sr, n_steps=VOICE_PITCH_STEPS)
            audio = audio.astype('float32')
            
            # Play via sounddevice at 24000 Hz as specified
            sd.play(audio, sr)
            sd.wait()
        except Exception as e:
            print(f"[TTS FALLBACK - pyttsx3]")
            try:
                engine = pyttsx3.init()
                engine.say(text)
                engine.runAndWait()
            except Exception:
                # Fallback to console print on TTS failure
                print(f"[TTS FALLBACK] {text}")
