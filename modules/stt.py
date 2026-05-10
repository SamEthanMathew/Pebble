"""Speech-to-Text module — uses speech_recognition + pyaudio."""

from __future__ import annotations
from .base import PebbleModule


class STTModule(PebbleModule):
    name         = 'stt'
    display_name = 'Speech Input'
    description  = 'Talk to Pebble — press mic and speak, Pebble transcribes your voice'
    icon         = '🎤'
    config_fields = [
        {'key': 'language', 'label': 'Language code (default: en-US)', 'type': 'text'},
    ]

    def is_ready(self) -> bool:
        try:
            import speech_recognition  # noqa: F401
            return True
        except ImportError:
            return False

    def tool_name(self) -> str:
        return 'transcribe_audio'

    def tool_description(self) -> str:
        return 'Transcribe a voice recording to text'

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['record', 'status'],
                    'description': (
                        'record — start recording and return the transcript; '
                        'status — return whether the microphone is available'
                    ),
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'record', **_) -> str:
        if action == 'status':
            try:
                import speech_recognition  # noqa: F401
                import pyaudio             # noqa: F401
                return 'Speech recognition and microphone are available.'
            except ImportError as e:
                return f'Missing dependency: {e}'

        # action == 'record'
        ok, result = record_once(language=self.cfg.get('language', 'en-US'))
        return result


# ── Standalone helper (used directly by chat_server) ──────────────────────────

def record_once(language: str = 'en-US') -> tuple[bool, str]:
    """
    Records one phrase and returns (success, text_or_error).
    Call from a background thread.
    """
    try:
        import speech_recognition as sr
    except ImportError:
        return False, 'speech_recognition not installed — run: pip install SpeechRecognition pyaudio'

    try:
        r = sr.Recognizer()
        r.energy_threshold      = 300
        r.dynamic_energy_threshold = True
        r.pause_threshold       = 0.8

        with sr.Microphone() as source:
            r.adjust_for_ambient_noise(source, duration=0.5)
            audio = r.listen(source, timeout=10, phrase_time_limit=30)

        text = r.recognize_google(audio, language=language)
        return True, text

    except sr.UnknownValueError:
        return False, 'Could not understand audio — please try again'
    except sr.RequestError:
        return False, 'Speech service unavailable — check internet'
    except OSError:
        return False, 'No microphone found — check your audio settings'
    except sr.WaitTimeoutError:
        return False, 'No speech detected — try again'
    except Exception as exc:
        return False, f'Voice error: {exc}'
