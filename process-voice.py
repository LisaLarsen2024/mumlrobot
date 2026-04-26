#!/usr/bin/env python3
"""
voice-to-claude — pipeline
Watches a folder for new audio, transcribes via Whisper, asks Claude, replies via Telegram.

All paths and credentials read from environment variables (see .env.example).
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        sys.stderr.write(f"Missing required env var: {name}\n")
        sys.exit(1)
    return val or ""


BASE = Path(env("VOICE_BASE_DIR", required=True))
INBOXES_RAW = env("VOICE_INBOXES", str(BASE / "voice-inbox"))
INBOXES = [Path(p.strip()).expanduser() for p in INBOXES_RAW.split(":") if p.strip()]
ARCHIVE = BASE / "voice-archive"
LOGS = BASE / "logs"
LOCK = BASE / ".lock"

TELEGRAM_TOKEN = env("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", required=True)

WHISPER_MODEL = env("WHISPER_MODEL", "turbo")
WHISPER_LANGUAGE = env("WHISPER_LANGUAGE", "en")
WHISPER_INITIAL_PROMPT = env("WHISPER_INITIAL_PROMPT", "")

CLAUDE_BIN = env("CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT = int(env("CLAUDE_TIMEOUT_SECONDS", "180"))
CLAUDE_SYSTEM_PROMPT = env(
    "CLAUDE_SYSTEM_PROMPT",
    "You are a personal voice assistant. Reply concisely (max 3 bullet points). "
    "Use emoji for visual structure. Confirm what you heard, then act.",
)

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac"}
HALLUCINATION_PATTERNS = [
    p.strip().lower() for p in env(
        "WHISPER_HALLUCINATION_PATTERNS",
        "subtitles by,closed captions,amara.org",
    ).split(",") if p.strip()
]
SILENCE_DB_THRESHOLD = float(env("SILENCE_DB_THRESHOLD", "-28"))

LOGS.mkdir(parents=True, exist_ok=True)
log_file = LOGS / f"voice-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger("voice-to-claude")


def telegram_send(text: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        ).encode()
        with urllib.request.urlopen(url, data=data, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception as exc:
        log.error(f"Telegram error: {exc}")
        return False


def is_file_stable(path: Path, wait: int = 2) -> bool:
    try:
        size_a = path.stat().st_size
        time.sleep(wait)
        size_b = path.stat().st_size
        return size_a == size_b and size_a > 0
    except FileNotFoundError:
        return False


def check_volume(audio: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(audio), "-af", "volumedetect",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        for line in result.stderr.splitlines():
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].strip().split()[0])
    except Exception as exc:
        log.warning(f"Volume check failed: {exc}")
    return None


def transcribe(audio: Path, output_dir: Path) -> str | None:
    log.info(f"Transcribing {audio.name}...")
    cmd = [
        "whisper", str(audio),
        "--model", WHISPER_MODEL,
        "--language", WHISPER_LANGUAGE,
        "--output_format", "txt",
        "--output_dir", str(output_dir),
        "--condition_on_previous_text", "False",
        "--verbose", "False",
    ]
    if WHISPER_INITIAL_PROMPT:
        cmd.extend(["--initial_prompt", WHISPER_INITIAL_PROMPT])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error(f"Whisper failed: {result.stderr[:500]}")
            return None
        txt_file = output_dir / (audio.stem + ".txt")
        if txt_file.exists():
            return txt_file.read_text().strip()
    except subprocess.TimeoutExpired:
        log.error("Whisper timeout (>5 min)")
    except Exception as exc:
        log.exception(f"Whisper error: {exc}")
    return None


def is_hallucination(text: str) -> bool:
    if not text or len(text.strip()) < 4:
        return True
    lower = text.lower()
    return any(pattern in lower for pattern in HALLUCINATION_PATTERNS)


def call_claude(transcription: str) -> str | None:
    log.info("Asking Claude...")
    prompt = (
        f"{CLAUDE_SYSTEM_PROMPT}\n\n"
        f'User said (via voice memo): "{transcription}"\n\n'
        f"Your reply (concise, action-oriented):"
    )
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--print", prompt],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
            cwd=str(BASE),
        )
        if result.returncode != 0:
            log.error(f"Claude failed: {result.stderr[:500]}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.error(f"Claude timeout (>{CLAUDE_TIMEOUT}s)")
    except Exception as exc:
        log.exception(f"Claude error: {exc}")
    return None


def process_one(audio: Path) -> bool:
    log.info(f"=== Processing {audio.name} ===")

    if not is_file_stable(audio):
        log.info(f"File still being written, skip: {audio.name}")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_dir = ARCHIVE / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived_audio = archive_dir / audio.name
    shutil.move(str(audio), str(archived_audio))

    try:
        db = check_volume(archived_audio)
        if db is not None:
            log.info(f"Mean volume: {db} dB")
            if db < SILENCE_DB_THRESHOLD:
                log.warning(f"Low volume ({db} dB) — hallucination risk")

        text = transcribe(archived_audio, archive_dir)
        if not text:
            telegram_send("⚠️ Could not transcribe audio.")
            return False

        (archive_dir / "transcription.txt").write_text(text)
        log.info(f"Transcription ({len(text)} chars): {text[:120]}")

        if is_hallucination(text):
            log.warning(f"Hallucination detected: {text}")
            telegram_send(
                f"⚠️ Audio too quiet — Whisper hallucinated.\n"
                f'Got only: "{text}"\n'
                f"Try again closer to the microphone."
            )
            return False

        telegram_send(f'🎙️ Heard: "{text}"\n\n💭 Thinking...')

        response = call_claude(text)
        if not response:
            telegram_send("⚠️ Claude did not reply in time. Check the log.")
            return False

        (archive_dir / "response.md").write_text(response)
        telegram_send(response)
        log.info(f"=== Done: {audio.name} ===")
        return True

    except Exception as exc:
        log.exception(f"Error processing {audio.name}")
        telegram_send(f"❌ Error: {str(exc)[:200]}")
        return False


def main() -> int:
    for inbox in INBOXES:
        inbox.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    if LOCK.exists():
        try:
            age = time.time() - LOCK.stat().st_mtime
            if age < 600:
                return 0
            log.warning(f"Stale lock ({age:.0f}s old), removing")
        except FileNotFoundError:
            pass
        LOCK.unlink(missing_ok=True)

    audio_files = []
    for inbox in INBOXES:
        if not inbox.exists():
            continue
        for f in inbox.iterdir():
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                audio_files.append(f)
    audio_files.sort(key=lambda p: p.stat().st_mtime)

    if not audio_files:
        return 0

    LOCK.touch()
    try:
        log.info(f"Found {len(audio_files)} audio file(s) to process")
        for audio in audio_files:
            process_one(audio)
    finally:
        LOCK.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
