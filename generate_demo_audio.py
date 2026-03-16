"""
Generate synthetic NVIDIA-style earnings call audio for demo purposes.

The script uses a realistic CEO earnings call script with specific numerical
claims that will be extracted and verified by the EarningsLens pipeline.

Numbers are based on NVIDIA Q3 FY2025 actual results (10-Q filed Nov 2024).
"""

import subprocess
import sys
from pathlib import Path

try:
    from gtts import gTTS
except ImportError:
    print("ERROR: gtts not installed. Run: pip install gtts")
    sys.exit(1)

SCRIPT = """
Good afternoon, and thank you for joining NVIDIA's third quarter fiscal year 2025 earnings call.
I'm Jensen Huang, CEO of NVIDIA. I'll start with some highlights, and then we'll open it up for questions.

We had an exceptional quarter. Revenue was thirty five point one billion dollars, up ninety four percent year over year, and up seventeen percent from the prior quarter.
This was a record quarter for NVIDIA across multiple dimensions.

Our Data Center segment was the primary driver of growth.
Data Center revenue reached thirty point eight billion dollars in Q3, representing an increase of one hundred and twelve percent compared to the same quarter last year.
Demand for our Hopper architecture GPU computing platform continues to be extraordinary.

Gaming revenue was three point three billion dollars, up fifteen percent year over year.
Our GeForce RTX series continues to gain traction with gamers and AI-powered applications.

Professional Visualization revenue was four hundred and eighty six million dollars, up seventeen percent year over year.
Automotive revenue was four hundred and forty nine million dollars, up seventy two percent year over year,
as automakers accelerate their adoption of NVIDIA DRIVE platforms for autonomous vehicle development.

On the profitability side, GAAP gross margin was seventy four point six percent for the quarter.
Non-GAAP gross margin was seventy five percent.
GAAP earnings per diluted share was seventy eight cents, up one hundred and eleven percent year over year.

Looking ahead to Q4 fiscal year 2025, we expect revenue of approximately thirty seven point five billion dollars,
plus or minus two percent. We expect GAAP and non-GAAP gross margins of approximately seventy three point five percent and seventy five percent respectively.

In summary, we believe we are at an inflection point for AI computing.
Every major cloud provider, enterprise, and sovereign nation is accelerating their investment in AI infrastructure.
NVIDIA is uniquely positioned to enable this next wave of AI-driven productivity.

Thank you. We'll now open the line for questions.

Operator, please go ahead.
"""

def main():
    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)

    mp3_path = output_dir / "nvidia_earnings_demo.mp3"
    wav_path = output_dir / "nvidia_earnings_demo.wav"
    wav_16k_path = output_dir / "nvidia_earnings_demo_16k.wav"

    print("Generating TTS audio...")
    tts = gTTS(text=SCRIPT.strip(), lang="en", slow=False)
    tts.save(str(mp3_path))
    print(f"MP3 saved: {mp3_path} ({mp3_path.stat().st_size:,} bytes)")

    # Convert to WAV for AWS Transcribe compatibility
    print("Converting to WAV...")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3_path), str(wav_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        print("Skipping WAV conversion — MP3 will work fine with AWS Transcribe")
    else:
        print(f"WAV saved: {wav_path} ({wav_path.stat().st_size:,} bytes)")

        # Also create 16kHz mono version for Nova Sonic / smoke tests
        result2 = subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path), "-ar", "16000", "-ac", "1", str(wav_16k_path)],
            capture_output=True, text=True
        )
        if result2.returncode == 0:
            print(f"16kHz WAV saved: {wav_16k_path} ({wav_16k_path.stat().st_size:,} bytes)")
        else:
            print("16kHz conversion skipped")

    print("\nDone! Claims embedded in the audio:")
    claims = [
        "Revenue: $35.1B (+94% YoY)",
        "Data Center revenue: $30.8B (+112% YoY)",
        "Gaming revenue: $3.3B (+15% YoY)",
        "Professional Visualization: $486M (+17% YoY)",
        "Automotive revenue: $449M (+72% YoY)",
        "GAAP gross margin: 74.6%",
        "GAAP EPS: $0.78 (+111% YoY)",
        "Q4 guidance: ~$37.5B revenue",
    ]
    for c in claims:
        print(f"  • {c}")

    print(f"\nUpload {mp3_path} or {wav_path} to the EarningsLens demo.")


if __name__ == "__main__":
    main()
