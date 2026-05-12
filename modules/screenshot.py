"""ScreenshotModule — captures the screen and lets the AI analyze it."""

from __future__ import annotations
import io
import base64
from datetime import datetime
from pathlib import Path

from .base import ActionTier, PebbleModule


class ScreenshotModule(PebbleModule):
    name         = 'screenshot'
    display_name = 'Screen Context'
    description  = "Let Pebble see your screen — captures a screenshot and describes what it sees"
    icon         = '🖥️'
    config_fields: list[dict] = []

    _default_tiers = {
        'capture': ActionTier.NOTIFY,
    }

    def is_ready(self) -> bool:
        try:
            from PIL import ImageGrab  # noqa: F401
            return True
        except ImportError:
            return False

    def tool_name(self) -> str:
        return 'take_screenshot'

    def tool_description(self) -> str:
        return (
            "Take a screenshot of the current screen. "
            "Use when the user asks 'what do you see?', 'look at my screen', "
            "'help me with this', or needs visual context."
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'region': {
                    'type': 'string',
                    'description': "Optional: 'full' (default) or 'active_window'",
                }
            },
            'required': [],
        }

    def execute(self, region: str = 'full', **_) -> str:
        try:
            from PIL import ImageGrab, Image

            img = ImageGrab.grab()

            # Resize to max 1280px wide (maintain aspect ratio)
            if img.width > 1280:
                ratio = 1280 / img.width
                img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)

            # Save to ~/.pebble/last_screenshot.jpg
            path = Path.home() / '.pebble' / 'last_screenshot.jpg'
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(path), 'JPEG', quality=85)

            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return f'[SCREENSHOT_TAKEN] Screenshot captured at {timestamp}. Path: {path}'

        except Exception as exc:
            return f'Screenshot failed: {exc}'


# ── Standalone helper ──────────────────────────────────────────────────────────

def capture_screen() -> tuple[str, str]:
    """
    Captures screen, saves to ~/.pebble/last_screenshot.jpg.
    Returns (base64_jpeg_string, file_path).
    """
    from PIL import ImageGrab, Image

    img = ImageGrab.grab()

    # Resize if wider than 1280px
    if img.width > 1280:
        ratio = 1280 / img.width
        img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)

    # Save to file
    path = Path.home() / '.pebble' / 'last_screenshot.jpg'
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), 'JPEG', quality=85)

    # Also return as base64
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return b64, str(path)
