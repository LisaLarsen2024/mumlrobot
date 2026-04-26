# mumlrobot

> Talk to Claude from your iPhone. Hear back in Telegram.
> Built in one evening. Costs $0/month if you have Claude Max.

## What this is

A pipeline that turns your iPhone into a voice-first interface to Claude Code on your Mac.

You record a voice memo on your phone. iCloud syncs it to your Mac. A LaunchAgent picks it up, runs Whisper, asks Claude, and posts the reply to your private Telegram bot. The whole round-trip takes 30–60 seconds and works from anywhere — no servers, no API costs.

## Why this exists

If you have ADHD, ideas evaporate the moment you can't write them down. Voice memos are the closest thing to "thinking out loud and having it remembered." This pipeline takes that one step further: your voice memos don't just get stored — they get *understood* and *acted on*.

It was built by a woman with two small kids and three jobs, in one evening, sitting in her kitchen. If she can build it, you can run it.

## Architecture

```
🎙️  iPhone Voice Memos (via iOS Shortcut)
     ↓ iCloud Drive sync
📁  Mac: ~/.../iCloud Drive/voice-inbox/
     ↓ LaunchAgent watches folder
🎧  Whisper (local, free, runs on your Mac)
     ↓ transcription
🤖  Claude Code CLI (uses your Claude Max subscription)
     ↓ reply
📱  Telegram bot → your phone
```

Three things make this cheap and reliable:

- **iCloud Drive** is the iPhone↔Mac bridge — Apple already does the hard sync work.
- **Whisper** runs locally — no API calls, no per-minute charges.
- **Claude Code CLI** uses your Claude Max subscription, so each call is part of your existing flat-rate plan instead of metered API usage.

## Requirements

- macOS (tested on macOS 26 "Tahoe")
- iPhone with iCloud Drive enabled, signed in to the same Apple ID as the Mac
- [Homebrew](https://brew.sh)
- Python 3.10+
- [`whisper`](https://github.com/openai/whisper) (`brew install openai-whisper` or `pip install -U openai-whisper`)
- [`ffmpeg`](https://ffmpeg.org) (`brew install ffmpeg`)
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) — and a Claude Max plan if you want zero per-message cost
- A Telegram account

## Setup (about 30 minutes)

### 1. Clone and install

```bash
git clone https://github.com/LisaLarsen2024/mumlrobot.git
cd mumlrobot
chmod +x voice-watcher-wrapper.sh process-voice.py
mkdir -p voice-inbox voice-archive logs
```

### 2. Create a Telegram bot

1. Open Telegram, search for **@BotFather**.
2. Send `/newbot` and follow the prompts. Choose a unique username ending in `_bot`.
3. BotFather gives you a token like `1234567890:ABC-...`. Copy it.
4. Search for your new bot in Telegram and tap **Start** — Telegram will not let bots message you until you initiate.
5. Get your `chat_id`:
   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" \
     | python3 -c "import json,sys; print([u['message']['chat']['id'] for u in json.load(sys.stdin)['result']])"
   ```

### 3. Configure

```bash
cp .env.example .env
$EDITOR .env
```

Fill in `VOICE_BASE_DIR`, `VOICE_INBOXES`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Adjust `WHISPER_LANGUAGE` if you don't speak English.

```bash
chmod 600 .env
```

### 4. Test the pipeline manually

```bash
# Generate a test audio file with macOS `say`
say -o /tmp/test.aiff "Hello, this is a pipeline test."
ffmpeg -i /tmp/test.aiff -c:a aac -b:a 96k voice-inbox/test.m4a

# Run the pipeline
./voice-watcher-wrapper.sh
```

You should see logs in `logs/voice-YYYY-MM-DD.log`, an archive folder under `voice-archive/`, and a message in your Telegram bot.

### 5. Install the LaunchAgent

Edit `launchagent.plist.template`: replace `YOURNAME` with your username. Then:

```bash
cp launchagent.plist.template ~/Library/LaunchAgents/com.example.mumlrobot.plist
launchctl load ~/Library/LaunchAgents/com.example.mumlrobot.plist
```

The agent runs the pipeline whenever a file lands in `voice-inbox/` (via `WatchPaths`) and also polls every 60 seconds as a backup.

### 6. Build the iOS Shortcut

On your iPhone:

1. Open **Shortcuts** → **+** new shortcut → name it **"Send to Claude"**.
2. Add action: **Make Recording** ("Opprett et opptak" in Norwegian). Use **Dagens dato** / **Current Date** as the recording name so files don't collide.
3. Add action: **Save File**. Connect input to the recording from step 2. Turn off "Ask Where to Save". Set destination to **iCloud Drive** → the folder you configured in `VOICE_INBOXES`.
4. Test the shortcut by tapping ▶︎. Talk for a few seconds. Within a minute, your Telegram bot should reply.

### 7. (Optional) Bind to the Action Button

iPhone 15 Pro and later: **Settings → Action Button → Shortcut → Send to Claude**. Now holding the button starts a recording from anywhere.

## Customization

Everything interesting is in `.env`:

- `WHISPER_INITIAL_PROMPT` — bias the transcription toward your vocabulary (proper nouns, jargon, family names). Big quality boost.
- `CLAUDE_SYSTEM_PROMPT` — shape Claude's voice. Tell it who you are, how you want to be talked to, what to do with brain dumps vs commands.
- `WHISPER_HALLUCINATION_PATTERNS` — Whisper will sometimes "hallucinate" subtitle credits when input is silent. Add patterns you see in your logs.

## Why a Telegram bot instead of an iOS notification?

Telegram is searchable, persistent, free, and works on every device you own. Notifications disappear; your brain dumps shouldn't. You can also share specific messages out of Telegram easily if a thought turns out to be useful for someone else.

## Troubleshooting

- **`Operation not permitted` from the LaunchAgent**: macOS sandboxing blocks `python3` from reading `~/Desktop/`. Use the bash wrapper (default), or move the project somewhere outside protected folders.
- **`X | None` syntax errors**: you're running Python 3.9 (Apple's bundled version). Install Homebrew Python 3.10+ and make sure it comes first in `PATH`.
- **No reply in Telegram, no logs**: confirm the LaunchAgent is loaded with `launchctl list | grep mumlrobot`. If `last exit code` is non-zero, check `logs/launchd-stderr.log`.
- **Whisper transcribes audio as "subtitles by..."**: your audio was effectively silent. Speak louder, or closer to the mic. The pipeline auto-detects this.

## Security notes

- `.env` is in `.gitignore`. Keep it that way.
- The Telegram bot can only message you — bots cannot DM users who haven't `/start`-ed them.
- Whisper runs locally; your audio never leaves your machine for transcription.
- Claude API calls go through your Claude Code session, governed by your Claude Max plan's privacy settings.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built collaboratively by [Lisa Sveen Larsen](https://skrivakademisk.no) and Claude Code in one evening, April 2026. The original system was Norwegian-language; this repository is the sanitized, generalized version.
