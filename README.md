# AgentOn Solulu Quest Auto-Completion Skill

A GitHub-hosted callable Skill for the AgentOn Solulu task:

1. Join Telegram community: `https://t.me/SoluluUS`
2. Register a Solulu account: `https://solulu.cc/register`
3. Capture Telegram group screenshot
4. Extract and return Solulu UID

This Skill uses Playwright browser automation. It can complete the registration flow automatically **when an OTP is supplied by the scheduler or fetched from an authorized mailbox**. It does not bypass OTP/CAPTCHA, does not create fake accounts, and does not fabricate screenshots.

## Install on EC2

```bash
git clone https://github.com/Sleipnirs/finchip-agenton-autocomplete-skill.git
cd finchip-agenton-autocomplete-skill
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

On Amazon Linux, if browser dependencies are missing:

```bash
sudo dnf install -y atk at-spi2-atk cups-libs libdrm libXcomposite libXdamage libXrandr mesa-libgbm pango alsa-lib
```

## Run plan mode

```bash
python3 agenton_autocomplete_skill.py examples/plan.json
```

## Run auto-completion mode

Edit `examples/auto_complete.json`, or pass credentials through environment variables:

```bash
export SOLULU_EMAIL='your-email@example.com'
export SOLULU_PASSWORD='Use-A-Strong-Password-123!'
```

Then run:

```bash
python3 agenton_autocomplete_skill.py examples/auto_complete.json
```

For one-off OTP:

```bash
echo '{"mode":"auto_complete","email":"you@example.com","password":"Use-A-Strong-Password-123!","otp":"123456","headless":false}' | python3 agenton_autocomplete_skill.py
```

For automatic OTP retrieval from an authorized mailbox:

```bash
export IMAP_HOST='imap.example.com'
export IMAP_USER='your-email@example.com'
export IMAP_PASSWORD='your-mailbox-app-password'
export IMAP_MAILBOX='INBOX'
python3 agenton_autocomplete_skill.py examples/auto_complete.json
```

## Telegram session

Telegram may require login/phone confirmation. Run once with `headless:false` and login with an authorized Telegram account. The persistent browser profile in `./.browser-state` can be reused by future runs.

The Skill captures a real screenshot at:

```text
./evidence/telegram_solulu.png
```

The Skill captures Solulu UID evidence at:

```text
./evidence/solulu_uid.png
```


## UID-only recovery mode

If the first run registered/logged in successfully but landed on the Assets page, rerun UID extraction without repeating OTP registration:

```bash
echo '{"mode":"extract_uid","email":"your-email@example.com","password":"Use-A-Strong-Password-123!","headless":true,"browser_state_dir":"./.browser-state","evidence_dir":"./evidence"}' | python3 agenton_autocomplete_skill.py
```

This mode reuses the persistent browser profile, opens Solulu, clicks Account / Personal Center, captures `./evidence/solulu_uid.png`, and returns the UID if found.

## Expected output

```json
{
  "ok": true,
  "skill": "agenton-solulu-quest-autocomplete",
  "version": "0.2.2",
  "mode": "auto_complete",
  "tasks": {
    "telegram_join": {
      "telegram_url": "https://t.me/SoluluUS",
      "screenshot_path": "./evidence/telegram_solulu.png"
    },
    "solulu_register": {
      "register_url": "https://solulu.cc/register",
      "uid": "42876717",
      "uid_found": true,
      "solulu_screenshot_path": "./evidence/solulu_uid.png"
    }
  },
  "submission": {
    "solulu_uid": "42876717",
    "telegram_screenshot_path": "./evidence/telegram_solulu.png",
    "ready_for_agenton_submission": true
  }
}
```

## External partner API note

The earlier draft referenced a partner API for ordering X followers / Telegram members. This Skill does not execute artificial engagement orders. It completes the real task flow through an authorized browser session and captures real evidence.
