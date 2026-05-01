#!/usr/bin/env python3
"""
Lisa Voice Assistant — pipeline-script
Watcher som behandler nye lydfiler i voice-inbox/.
Flyt: ny lydfil → Whisper turbo → Claude --print → Telegram-svar
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

BASE = Path.home() / "Desktop/workspace/familie/voice-assistant"
ICLOUD_INBOX = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/Lisa-Assistant/voice-inbox"
)
LOCAL_INBOX = BASE / "voice-inbox"
INBOXES = [LOCAL_INBOX, ICLOUD_INBOX]
ARCHIVE = BASE / "voice-archive"
LOGS = BASE / "logs"
LOCK = BASE / ".lock"
STATE = Path.home() / ".openclaw/state"

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac"}
HALLUCINATION_PATTERNS = [
    "undertekster av", "teksting av", "subtitles by",
    "ai-media", "nicolai winther", "amara.org",
]

LOGS.mkdir(parents=True, exist_ok=True)
log_file = LOGS / f"voice-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger("voice-assistant")


def read_secret(name: str) -> str:
    return (STATE / name).read_text().strip()


def telegram_send(text: str) -> bool:
    try:
        token = read_secret("lisa-assistant-token")
        chat_id = read_secret("lisa-assistant-chat-id")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        with urllib.request.urlopen(url, data=data, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception as exc:
        log.error(f"Telegram-feil: {exc}")
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
        log.warning(f"Volum-sjekk feilet: {exc}")
    return None


def transcribe(audio: Path, output_dir: Path) -> str | None:
    log.info(f"Transkriberer {audio.name}...")
    try:
        result = subprocess.run(
            [
                "whisper", str(audio),
                "--model", "turbo",
                "--language", "no",
                "--output_format", "txt",
                "--output_dir", str(output_dir),
                "--condition_on_previous_text", "False",
                "--initial_prompt",
                "Lisa snakker norsk om jobb, familie, hverdag, Skriv Akademisk, Statped.",
                "--verbose", "False",
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            log.error(f"Whisper feilet: {result.stderr[:500]}")
            return None
        txt_file = output_dir / (audio.stem + ".txt")
        if txt_file.exists():
            return txt_file.read_text().strip()
    except subprocess.TimeoutExpired:
        log.error("Whisper tidsavbrudd (>5 min)")
    except Exception as exc:
        log.exception(f"Whisper-feil: {exc}")
    return None


def is_hallucination(text: str) -> bool:
    if not text or len(text.strip()) < 4:
        return True
    lower = text.lower()
    return any(pattern in lower for pattern in HALLUCINATION_PATTERNS)


CLAUDE_SYSTEM_PROMPT = """Du er Lisa Larsens personlige stemmeassistent.

Lisa har ADHD, lever et hektisk liv som mor til to (Tobias og Idun),
prosjektleder i Statped, og leder for Skriv Akademisk AS.
Hun snakker inn voice memos fra iPhonen sin og du svarer kort via Telegram.

Verdiene dine: VOKTER (trygghet, ADHD-vennlig, faktisk smart).
Lisas grunnverdier: Justice, Peace, Love.

=== HANDLINGSREGLER (BRUK MCP-VERKTØY) ===

📅 KALENDER (Google Calendar MCP):
- Hvis Lisa nevner møte, avtale, frist, påminnelse med tidspunkt:
  → Bruk mcp__claude_ai_Google_Calendar__create_event
  → Standardkalender: "Lisa Privat" hvis ikke noe annet er klart
  → Møter med Øyvind/familie: "Lisa & Øyvind" eller "Familie"
  → Skriv Akademisk-arbeid: "Skriv Akademisk AS"
  → Bekreft i svaret: "✅ Lagt i kalenderen: [tittel] [dato] [tid]"
- Hvis usikker på tid: bruk suggest_time eller spør i svaret

📧 GMAIL (kun UTKAST, ALDRI send):
- Hvis Lisa vil sende e-post:
  → Bruk mcp__claude_ai_Gmail__create_draft (ALDRI andre Gmail-verktøy)
  → Inkluder: til, emne, innhold (varmt, profesjonelt, signer "Vennlig hilsen, Lisa")
  → Bekreft i svaret: "📧 Utkast laget: [emne] til [mottaker]. Sjekk Gmail før du sender."

📝 BRAIN DUMP (ingen handling, bare struktur):
- Hvis Lisa bare tenker høyt: ingen kalender/Gmail-handlinger
- Bare bekreft hva du fanget, oppsummer kjernen i 1-3 punkter

=== SVARREGLER ===
- Maks 3 punkter, korte setninger, Telegram-skjerm er liten
- Emoji for visuell struktur (✅ 📝 🤔 🛒 📅 📧 💛)
- Norsk, varm, profesjonell — som en god kollega
- ALDRI moralisering, ALDRI selvgransking
- ALDRI lange tekstvegger
- Avslutt naturlig — ikke "trenger du noe annet?"

=== SIKKERHET ===
- ALDRI send e-post (kun utkast)
- ALDRI slett kalenderhendelser uten eksplisitt forespørsel
- ALDRI rør filer eller skall — kun de tre MCP-verktøyene listet over"""


ALLOWED_TOOLS = ",".join([
    "mcp__claude_ai_Google_Calendar__create_event",
    "mcp__claude_ai_Google_Calendar__list_events",
    "mcp__claude_ai_Google_Calendar__list_calendars",
    "mcp__claude_ai_Google_Calendar__suggest_time",
    "mcp__claude_ai_Google_Calendar__update_event",
    "mcp__claude_ai_Google_Calendar__get_event",
    "mcp__claude_ai_Gmail__create_draft",
    "mcp__claude_ai_Gmail__list_drafts",
    "mcp__claude_ai_Gmail__search_threads",
])


def call_claude(transcription: str) -> str | None:
    log.info("Spør Claude...")
    prompt = (
        f"{CLAUDE_SYSTEM_PROMPT}\n\n"
        f"Lisa sa (via voice memo): \"{transcription}\"\n\n"
        f"Ditt svar (kort, ADHD-vennlig, handlingsorientert):"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--allowed-tools", ALLOWED_TOOLS],
            capture_output=True, text=True, timeout=240,
            cwd=str(BASE),
        )
        if result.returncode != 0:
            log.error(f"Claude feilet: {result.stderr[:500]}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.error("Claude tidsavbrudd (>4 min)")
    except Exception as exc:
        log.exception(f"Claude-feil: {exc}")
    return None


def process_one(audio: Path) -> bool:
    log.info(f"=== Behandler {audio.name} ===")

    if not is_file_stable(audio):
        log.info(f"Filen blir fortsatt skrevet, hopper over: {audio.name}")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_dir = ARCHIVE / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived_audio = archive_dir / audio.name
    shutil.move(str(audio), str(archived_audio))
    log.info(f"Arkivert til {archive_dir}")

    try:
        db = check_volume(archived_audio)
        if db is not None:
            log.info(f"Snittvolum: {db} dB")
            if db < -28:
                log.warning(f"Lav volum ({db} dB) — hallusinasjonsrisiko")

        text = transcribe(archived_audio, archive_dir)
        if not text:
            telegram_send("⚠️ Klarte ikke transkribere lydfilen.")
            return False

        (archive_dir / "transcription.txt").write_text(text)
        log.info(f"Transkripsjon ({len(text)} tegn): {text[:120]}")

        if is_hallucination(text):
            log.warning(f"Hallusinasjon oppdaget: {text}")
            telegram_send(
                f"⚠️ Lyden var for stille — Whisper hallusinerte.\n"
                f"Fanget bare: \"{text}\"\n"
                f"Prøv på nytt nærmere mikrofonen."
            )
            return False

        telegram_send(f"🎙️ Hørte: \"{text}\"\n\n💭 Tenker...")

        response = call_claude(text)
        if not response:
            telegram_send("⚠️ Claude svarte ikke i tide. Sjekk loggen.")
            return False

        (archive_dir / "response.md").write_text(response)
        telegram_send(response)
        log.info(f"=== Ferdig: {audio.name} ===")
        return True

    except Exception as exc:
        log.exception(f"Feil under behandling av {audio.name}")
        telegram_send(f"❌ Feil: {str(exc)[:200]}")
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
            log.warning(f"Stale lock ({age:.0f}s gammel), fjerner")
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
        log.info(f"Fant {len(audio_files)} lydfil(er) å behandle")
        for audio in audio_files:
            process_one(audio)
    finally:
        LOCK.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
