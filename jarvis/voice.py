"""Optional voice: microphone input and spoken replies.

Speech-to-text uses the third-party ``SpeechRecognition`` package (optional).
Text-to-speech uses whatever the operating system already has, so it works
without extra packages on macOS, Windows and most Linux desktops.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from typing import Optional, Tuple

MAX_SPOKEN_CHARS = 1200


class VoiceError(RuntimeError):
    """Raised when speech input cannot be used."""


def mic_available() -> Tuple[bool, str]:
    try:
        import speech_recognition  # noqa: F401
    except ImportError:
        return False, (
            "Voice input needs the SpeechRecognition package: "
            "pip install SpeechRecognition pyaudio"
        )
    return True, ""


def listen(timeout: float = 8.0, phrase_limit: float = 15.0) -> Optional[str]:
    """Listen for one phrase and return it as text, or None if nothing was heard."""
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise VoiceError("SpeechRecognition is not installed.") from exc

    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
    except sr.WaitTimeoutError:
        return None
    except (OSError, AttributeError) as exc:
        raise VoiceError("no microphone is available (%s)" % exc) from exc

    try:
        return recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return None
    except sr.RequestError as exc:
        raise VoiceError("speech recognition service unavailable (%s)" % exc) from exc


def speak(text: str) -> bool:
    """Speak text with the OS voice. Returns False when no engine is available."""
    clean = _clean_for_speech(text)
    if not clean:
        return False

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["say", clean],
                check=False,
                timeout=180,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        if system == "Windows":
            env = dict(os.environ)
            env["JARVIS_SPEAK_TEXT"] = clean
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak($env:JARVIS_SPEAK_TEXT)",
                ],
                check=False,
                timeout=180,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        for candidate in ("spd-say", "espeak-ng", "espeak"):
            executable = shutil.which(candidate)
            if executable:
                subprocess.run(
                    [executable, clean],
                    check=False,
                    timeout=180,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def _clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip("-")
    return text[:MAX_SPOKEN_CHARS]
