#!/usr/bin/env python3
"""Download a Piper voice into ``models/piper/``.

Usage::

    python scripts/download_voice.py                 # default voice
    python scripts/download_voice.py --voice en_GB-alba-medium
    python scripts/download_voice.py --list

Voices come from the official Piper releases on Hugging Face. Each voice is two
files: the ONNX model and its JSON config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = PROJECT_ROOT / "models" / "piper"
BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# name -> path inside the voices repository
VOICES: dict[str, str] = {
    "en_US-amy-medium": "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high.onnx",
    "en_US-joe-medium": "en/en_US/joe/medium/en_US-joe-medium.onnx",
    "en_GB-alba-medium": "en/en_GB/alba/medium/en_GB-alba-medium.onnx",
    "en_GB-northern_english_male-medium": (
        "en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx"
    ),
}

DEFAULT_VOICE = "en_US-amy-medium"


def download(url: str, destination: Path) -> None:
    """Stream a file to disk with a simple progress line."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        written = 0
        with partial.open("wb") as handle:
            for block in response.iter_bytes(chunk_size=262_144):
                handle.write(block)
                written += len(block)
                if total:
                    percent = written * 100 // total
                    print(
                        f"\r  {destination.name}: {percent:3d}%  "
                        f"({written / 1_048_576:.1f} MB)",
                        end="",
                        flush=True,
                    )
    partial.replace(destination)
    print(f"\r  {destination.name}: done ({written / 1_048_576:.1f} MB)      ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a Piper voice for Nova.")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="voice name to download")
    parser.add_argument("--list", action="store_true", help="list the known voices and exit")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    if args.list:
        print("Available voices:")
        for name in VOICES:
            marker = " (default)" if name == DEFAULT_VOICE else ""
            print(f"  {name}{marker}")
        return 0

    if args.voice not in VOICES:
        print(f"Unknown voice {args.voice!r}. Use --list to see the options.", file=sys.stderr)
        return 2

    relative = VOICES[args.voice]
    model_path = VOICE_DIR / f"{args.voice}.onnx"
    config_path = VOICE_DIR / f"{args.voice}.onnx.json"

    if model_path.exists() and config_path.exists() and not args.force:
        print(f"{args.voice} is already installed at {model_path}")
        print("Pass --force to download it again.")
        return 0

    print(f"Downloading {args.voice} into {VOICE_DIR}")
    try:
        download(f"{BASE_URL}/{quote(relative)}?download=true", model_path)
        download(f"{BASE_URL}/{quote(relative)}.json?download=true", config_path)
    except httpx.HTTPError as exc:
        print(f"\nDownload failed: {exc}", file=sys.stderr)
        return 1

    print("\nVoice installed. Point Nova at it with:")
    print(f"  NOVA_PIPER_VOICE_PATH=models/piper/{args.voice}.onnx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
