"""Always-listening voice mode: wake word, local speech recognition, spoken replies.

Everything in this module runs on your own machine: the wake word model
(openWakeWord, ~400 KB), speech recognition (faster-whisper) and the spoken
reply (Piper, with the OS voice as fallback). No audio leaves the computer.

The heavy pieces are optional dependencies, imported lazily so the normal
terminal assistant keeps working without them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import voice
from .agent import save_history
from .config import Config, session_file, state_dir
from .ui import UI

SAMPLE_RATE = 16000
CHUNK = 1280  # 80 ms of audio, the frame size openWakeWord expects
MAX_UTTERANCE_SECONDS = 15.0
SILENCE_TO_STOP = 0.9
MIN_SPEECH_SECONDS = 0.35
FLOOR_RMS = 120.0
STOP_PHRASES = {
    "stop",
    "stop listening",
    "goodbye",
    "good bye",
    "shut down",
    "shutdown",
    "that's all",
    "thats all",
    "go to sleep",
}

INSTALL_HINT = (
    "Always-listening needs a few optional packages:\n"
    '  python -m pip install -e ".[listen]"\n'
    "On Linux without PortAudio installed yet: sudo apt install libportaudio2\n"
    "For the nicer Piper voice: python -m pip install piper-tts"
)


# ------------------------------------------------------------------- components


class Microphone:
    """A 16 kHz mono microphone stream, opened on demand."""

    def __init__(self, rate: int = SAMPLE_RATE, chunk: int = CHUNK) -> None:
        try:
            import sounddevice  # noqa: F401
        except Exception as exc:
            raise voice.VoiceError(
                "microphone input is unavailable (%s).\n%s" % (exc, INSTALL_HINT)
            ) from exc
        self.rate = rate
        self.chunk = chunk
        self._stream = None

    def open(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        try:
            self._stream = sd.InputStream(
                samplerate=self.rate,
                blocksize=self.chunk,
                dtype="int16",
                channels=1,
            )
            self._stream.start()
        except Exception as exc:
            raise voice.VoiceError("cannot open the microphone: %s" % exc) from exc

    def read(self) -> Any:
        self.open()
        data, _overflowed = self._stream.read(self.chunk)
        return data.reshape(-1)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()


class WakeWord:
    """Scores short frames against a pretrained openWakeWord model."""

    def __init__(self, phrase: str = "hey_jarvis", threshold: float = 0.5) -> None:
        try:
            from openwakeword.model import Model
        except Exception as exc:
            raise voice.VoiceError(
                "the wake word is unavailable (%s).\n%s" % (exc, INSTALL_HINT)
            ) from exc
        try:
            self.model = Model(wakeword_models=[phrase], inference_framework="onnx")
        except Exception as exc:
            raise voice.VoiceError(
                "could not load wake word %r: %s\nKnown models: hey_jarvis, hey_mycroft, alexa"
                % (phrase, exc)
            ) from exc
        self.phrase = phrase
        self.threshold = max(0.05, min(0.95, float(threshold)))

    def triggered(self, frame: Any) -> bool:
        scores = self.model.predict(frame)
        if not scores:
            return False
        try:
            best = max(float(value) for value in scores.values())
        except (TypeError, ValueError):
            return False
        return best >= self.threshold

    def reset(self) -> None:
        try:
            self.model.reset()
        except Exception:
            pass


class WhisperTranscriber:
    """Speech to text with faster-whisper, running locally."""

    def __init__(self, model_name: str = "base.en") -> None:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise voice.VoiceError(
                "speech recognition is unavailable (%s).\n%s" % (exc, INSTALL_HINT)
            ) from exc
        try:
            self.model = WhisperModel(model_name, device="auto", compute_type="int8")
        except Exception as exc:
            raise voice.VoiceError(
                "could not load the speech model %r (it downloads once on first use): %s"
                % (model_name, exc)
            ) from exc
        self.model_name = model_name

    def transcribe(self, audio: Any) -> str:
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class Speaker:
    """Speaks text with Piper when available, otherwise with the OS voice."""

    def __init__(self, piper_voice: str = "") -> None:
        self.piper = shutil.which("piper")
        self.voice_path = _find_piper_voice(piper_voice)
        self.started = False

    @property
    def using_piper(self) -> bool:
        return bool(self.piper and self.voice_path)

    def speak(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        if self.using_piper and self._speak_piper(text):
            return True
        return voice.speak(text)

    def _speak_piper(self, text: str) -> bool:
        folder = Path(tempfile.mkdtemp(prefix="jarvis-voice-"))
        script = folder / "reply.txt"
        wav_path = folder / "reply.wav"
        try:
            script.write_text(text, encoding="utf-8")
            with script.open("r", encoding="utf-8") as stdin:
                completed = subprocess.run(
                    [self.piper, "--model", self.voice_path, "--output_file", str(wav_path)],
                    stdin=stdin,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=180,
                )
            if completed.returncode != 0 or not wav_path.is_file():
                return False
            return _play_wav(wav_path)
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            for path in (script, wav_path):
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass


def _find_piper_voice(configured: str) -> str:
    """An explicit voice path, else the first .onnx in ~/.jarvis/voices."""
    if configured:
        candidate = Path(os.path.expanduser(os.path.expandvars(configured)))
        if candidate.is_file():
            return str(candidate)
    folder = state_dir() / "voices"
    if folder.is_dir():
        for path in sorted(folder.glob("*.onnx")):
            if path.is_file():
                return str(path)
    return ""


def _rms(frame: Any) -> float:
    import numpy as np

    return float(np.sqrt(np.mean(np.square(frame.astype("float32")))))


def _play_wav(path: Path) -> bool:
    try:
        import numpy as np
        import sounddevice as sd

        with wave.open(str(path), "rb") as handle:
            if handle.getsampwidth() != 2:
                return False
            rate = handle.getframerate()
            channels = handle.getnchannels()
            raw = handle.readframes(handle.getnframes())
        data = np.frombuffer(raw, dtype="int16").astype("float32") / 32768.0
        if channels > 1:
            data = data.reshape(-1, channels)
        sd.play(data, rate)
        sd.wait()
        return True
    except Exception:
        for command in (["afplay"], ["aplay", "-q"], ["paplay"]):
            executable = shutil.which(command[0])
            if not executable:
                continue
            try:
                subprocess.run(
                    [executable] + command[1:] + [str(path)],
                    check=False,
                    timeout=180,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                return False
    return False


def phrase_text(model_name: str) -> str:
    return model_name.replace("_", " ").replace("-", " ").strip()


# -------------------------------------------------------------------- the loop


class VoiceLoop:
    def __init__(
        self,
        agent: Any,
        ui: UI,
        config: Config,
        audio: Any = None,
        wake: Any = None,
        transcriber: Any = None,
        speaker: Any = None,
        printer_factory: Optional[Callable[[], Any]] = None,
        on_command: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self.agent = agent
        self.ui = ui
        self.config = config
        self.audio = audio if audio is not None else Microphone()
        self.wake = wake if wake is not None else WakeWord(config.wake_word, config.wake_threshold)
        self.transcriber = (
            transcriber if transcriber is not None else WhisperTranscriber(config.stt_model)
        )
        self.speaker = speaker if speaker is not None else Speaker(config.piper_voice)
        self.printer_factory = printer_factory
        self.on_command = on_command
        self.stopping = False
        self.audio.open()

    # ------------------------------------------------------------------ public

    def run(self, max_turns: Optional[int] = None) -> None:
        turns = 0
        self.ui.info('Listening for "%s" - say it to talk, Ctrl+C to stop.' % phrase_text(self.config.wake_word))
        if not getattr(self.speaker, "using_piper", False):
            self.ui.info("Using your operating system's voice (install piper-tts for a nicer one).")
        try:
            while max_turns is None or turns < max_turns:
                if not self._wait_for_wake():
                    break
                turns += 1
                self._turn()
                if self.stopping:
                    break
        finally:
            self.close()

    def close(self) -> None:
        try:
            self.audio.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ internals

    def _wait_for_wake(self) -> bool:
        while not self.stopping:
            frame = self.audio.read()
            if frame is not None and self.wake.triggered(frame):
                self.wake.reset()
                return True
        return False

    def record_utterance(self, audio: Any, max_seconds: float = MAX_UTTERANCE_SECONDS) -> Any:
        """Record until the speaker pauses, then return float32 audio or None."""
        import numpy as np

        ambient = [_rms(audio.read()) for _ in range(4)]
        floor = max(FLOOR_RMS, (sum(ambient) / max(1, len(ambient))) * 2.0)
        frames: List[Any] = []
        silent = 0
        speech = 0
        limit = int(max_seconds * SAMPLE_RATE / CHUNK)
        for _ in range(limit):
            frame = audio.read()
            if frame is None:
                break
            frames.append(frame)
            if _rms(frame) >= floor:
                silent = 0
                speech += 1
            else:
                silent += 1
                if speech and silent * (CHUNK / float(SAMPLE_RATE)) >= SILENCE_TO_STOP:
                    break
        if not frames or speech * (CHUNK / float(SAMPLE_RATE)) < MIN_SPEECH_SECONDS:
            return None
        return np.concatenate(frames).astype("float32") / 32768.0

    def _turn(self) -> None:
        self.ui.info("Yes?")
        audio = self.record_utterance(self.audio)
        if audio is None:
            self.ui.info("(heard nothing)")
            return

        try:
            heard = self.transcriber.transcribe(audio)
        except Exception as exc:
            self.ui.error("could not understand that: %s" % exc)
            return
        heard = " ".join(str(heard or "").split())
        if not heard:
            self.ui.info("(heard nothing)")
            return
        self.ui.user_said(heard)

        if heard.lower().strip(" .!?") in STOP_PHRASES:
            self.speaker.speak("Goodbye.")
            self.stopping = True
            return

        if heard.startswith("/") and self.on_command is not None:
            if self.on_command(heard) == "exit":
                self.stopping = True
            return

        # Mute the microphone while thinking and talking so it cannot hear itself.
        self.audio.close()
        printer = self.printer_factory() if self.printer_factory else None
        try:
            if printer is None:
                reply = self.agent.chat(heard)
            else:
                reply = self.agent.chat(heard, on_token=printer, on_tool=printer.finish)
        except KeyboardInterrupt:
            self.ui.warn("Interrupted.")
            return
        except Exception as exc:
            self.ui.error(str(exc))
            return
        finally:
            if printer is not None:
                printer.finish()
            self.wake.reset()
            self.audio.open()

        save_history(session_file(), self.agent.history)
        if reply:
            self.speaker.speak(reply)
