import sounddevice as sd
import numpy as np
import librosa
import scipy.signal
from faster_whisper import WhisperModel
import pyttsx3
from kokoro_onnx import Kokoro

VOICE_PITCH_STEPS = 4
MIC_DEVICE_INDEX = 1
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

    # VAD configuration (RMS-based)
    SPEAKING_THRESHOLD = 0.09    # RMS volume threshold — raise if too sensitive
    SILENCE_FRAMES     = 20      # consecutive silent frames before stopping
    MAX_RECORD_SECONDS = 15      # safety cap
    FRAME_DURATION     = 0.03    # 30ms frames
    SAMPLE_RATE        = 16000
    FRAME_SIZE         = int(16000 * 0.03) # = 480 samples

    def listen(self):
        """
        Record audio using a simple RMS volume detection threshold.
        Starts capturing when volume stays above SPEAKING_THRESHOLD,
        stops when SILENCE_FRAMES consecutive silent frames are observed.
        Returns the transcribed text string, or '' if nothing was spoken.
        """
        if IRIS_SPEAKING:
            return ""

        frames = []
        silent_frames = 0
        speaking_started = False
        pre_buffer = []

        print("[VoiceManager] Listening...")

        # Each frame is 30ms; loop until safety cap is hit
        num_frames = int(self.MAX_RECORD_SECONDS / self.FRAME_DURATION)

        with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1,
                            dtype='float32', blocksize=self.FRAME_SIZE,
                            device=MIC_DEVICE_INDEX) as stream:
            for _ in range(num_frames):
                if IRIS_SPEAKING:
                    break

                frame, _ = stream.read(self.FRAME_SIZE)
                
                # Calculate RMS volume
                volume = np.sqrt(np.mean(np.square(frame)))
                is_speech = volume > self.SPEAKING_THRESHOLD

                if not speaking_started:
                    # Keep a small buffer of the most recent frames to capture speech onset
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
                    if not is_speech:
                        silent_frames += 1
                    else:
                        silent_frames = 0
                    
                    if silent_frames > self.SILENCE_FRAMES:
                        print("[VoiceManager] Silence detected, stopping.")
                        break

        if not frames or not speaking_started:
            return ""

        # Concatenate float32 frames for faster-whisper
        audio = np.concatenate(frames).flatten()
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

            # Show caption immediately before audio generation
            if avatar is not None:
                avatar.show_caption(text)

            # Generate audio using Kokoro
            audio, sr = self.kokoro.create(text, voice="af_sky", speed=1.0, lang="en-us")

            audio = librosa.effects.pitch_shift(audio.astype(float), sr=sr, n_steps=VOICE_PITCH_STEPS)
            audio = scipy.signal.resample_poly(audio, 44100, 24000).astype('float32')
            
            silence_pad = np.zeros(int(44100 * 0.3), dtype='float32')
            audio = np.concatenate([audio, silence_pad])

            # Signal avatar before playback
            if avatar is not None:
                avatar.set_state("speaking")

            # Play via sounddevice at 44100 Hz
            IRIS_SPEAKING = True
            try:
                sd.play(audio, samplerate=44100)
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
