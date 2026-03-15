"""simple_tts.py: Text-to-speech using gTTS (Google TTS) for briefing audio."""
import asyncio
from pathlib import Path

from gtts import gTTS


def synthesize(text: str, output_path: str) -> str:
    """Convert text to MP3 audio using gTTS."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tts = gTTS(text=text, lang="en", slow=False)
    tts.save(output_path)
    return output_path


async def synthesize_async(text: str, output_path: str) -> str:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, synthesize, text, output_path)
    return output_path
