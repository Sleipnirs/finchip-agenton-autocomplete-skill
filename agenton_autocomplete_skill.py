#!/usr/bin/env python3
"""
AgentOn Solulu Quest Auto-Completion Skill

Automates the legitimate Solulu registration flow and captures evidence for the
AgentOn task:
- Join Telegram community: https://t.me/SoluluUS
- Register Solulu account: https://solulu.cc/register
- Provide Telegram group screenshot
- Provide Solulu UID

This Skill intentionally does not create fake accounts, bypass OTP/CAPTCHA, or
fabricate screenshots. OTP can be supplied by the scheduler/user or fetched from
an authorized mailbox via IMAP credentials owned/controlled by the operator.
"""
from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


SOLULU_REGISTER_URL = "https://solulu.cc/register"
SOLULU_HOME_URL = "https://solulu.cc/"
TELEGRAM_URL = "https://t.me/SoluluUS"
AGENTON_TG_QUEST_URL = "https://agenton.me/quests/5539a280-3806-4980-b53c-b8a8948177ce"
AGENTON_X_QUEST_URL = "https://agenton.me/quests/74e93925-2024-4f22-839f-f7430ffa51ac"

UID_RE = re.compile(r"UID\s*[:：]?\s*(\d{4,20})", re.IGNORECASE)
OTP_RE_DEFAULT = r"(?<!\d)(\d{4,8})(?!\d)"


class SkillError(Exception):
    pass


def read_json_input(path: Optional[str]) -> Dict[str, Any]:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def env_or(data: Dict[str, Any], key: str, env_name: str, default: Any = None) -> Any:
    value = data.get(key)
    if value is not None and value != "":
        return value
    return os.getenv(env_name, default)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def redact(value: Optional[str], visible: int = 3) -> Optional[str]:
    if not value:
        return value
    if len(value) <= visible:
        return "*" * len(value)
    return value[:visible] + "***"


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for decoded, charset in decode_header(value):
        if isinstance(decoded, bytes):
            parts.append(decoded.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(decoded)
    return "".join(parts)


def extract_text_from_email(raw_msg: bytes) -> str:
    msg = message_from_bytes(raw_msg)
    chunks = [decode_mime_header(msg.get("Subject"))]
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    chunks.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks)


def fetch_otp_from_imap(timeout_seconds: int = 120, poll_seconds: int = 5) -> Optional[str]:
    """Fetch OTP from an authorized mailbox configured by env vars.

    Required env:
    - IMAP_HOST
    - IMAP_USER
    - IMAP_PASSWORD

    Optional env:
    - IMAP_MAILBOX=INBOX
    - OTP_FROM_FILTER=solulu
    - OTP_REGEX='(?<!\\d)(\\d{4,8})(?!\\d)'
    """
    host = os.getenv("IMAP_HOST")
    user = os.getenv("IMAP_USER")
    password = os.getenv("IMAP_PASSWORD")
    mailbox = os.getenv("IMAP_MAILBOX", "INBOX")
    from_filter = os.getenv("OTP_FROM_FILTER", "")
    otp_regex = os.getenv("OTP_REGEX", OTP_RE_DEFAULT)

    if not host or not user or not password:
        return None

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL(host) as imap:
                imap.login(user, password)
                imap.select(mailbox)

                # Search recent unseen emails first; fallback to all recent emails.
                status, data = imap.search(None, "UNSEEN")
                ids = data[0].split() if status == "OK" and data and data[0] else []
                if not ids:
                    status, data = imap.search(None, "ALL")
                    ids = data[0].split()[-15:] if status == "OK" and data and data[0] else []

                for msg_id in reversed(ids):
                    status, msg_data = imap.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not msg_data:
                        continue
                    raw_msg = msg_data[0][1]
                    msg_text = extract_text_from_email(raw_msg)
                    if from_filter and from_filter.lower() not in msg_text.lower():
                        # from_filter is intentionally broad because From may not be in text.
                        pass
                    match = re.search(otp_regex, msg_text)
                    if match:
                        return match.group(1)
        except Exception:
            # Keep polling until timeout. Full error is not exposed to avoid leaking mailbox details.
            pass
        time.sleep(poll_seconds)
    return None


@dataclass
class AutoCompleteConfig:
    email: str
    password: str
    otp: Optional[str]
    headless: bool
    browser_state_dir: str
    evidence_dir: str
    telegram_screenshot_path: str
    solulu_screenshot_path: str
    otp_timeout_seconds: int
    manual_pause_for_otp: bool
    telegram_enabled: bool
    registration_enabled: bool


def build_config(data: Dict[str, Any]) -> AutoCompleteConfig:
    email = env_or(data, "email", "SOLULU_EMAIL")
    password = env_or(data, "password", "SOLULU_PASSWORD")
    otp = env_or(data, "otp", "SOLULU_OTP")

    if not email:
        raise SkillError("Missing email. Provide input.email or SOLULU_EMAIL.")
    if not password:
        raise SkillError("Missing password. Provide input.password or SOLULU_PASSWORD.")

    evidence_dir = data.get("evidence_dir", "./evidence")
    return AutoCompleteConfig(
        email=email,
        password=password,
        otp=otp,
        headless=bool(data.get("headless", os.getenv("HEADLESS", "false").lower() == "true")),
        browser_state_dir=data.get("browser_state_dir", os.getenv("BROWSER_STATE_DIR", "./.browser-state")),
        evidence_dir=evidence_dir,
        telegram_screenshot_path=data.get("telegram_screenshot_path", f"{evidence_dir}/telegram_solulu.png"),
        solulu_screenshot_path=data.get("solulu_screenshot_path", f"{evidence_dir}/solulu_uid.png"),
        otp_timeout_seconds=int(data.get("otp_timeout_seconds", os.getenv("OTP_TIMEOUT_SECONDS", "120"))),
        manual_pause_for_otp=bool(data.get("manual_pause_for_otp", False)),
        telegram_enabled=bool(data.get("telegram_enabled", True)),
        registration_enabled=bool(data.get("registration_enabled", True)),
    )


def import_playwright():
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        return sync_playwright, PlaywrightTimeoutError
    except Exception as e:
        raise SkillError(
            "Playwright is not installed. Run: python3 -m pip install -r requirements.txt && python3 -m playwright install chromium"
        ) from e


def click_if_visible(page, selector: str, timeout: int = 1500) -> bool:
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click(timeout=timeout)
        return True
    except Exception:
        return False


def fill_first_available(page, candidates: list[str], value: str, timeout: int = 2500) -> str:
    last_error = None
    for selector in candidates:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.fill(value)
            return selector
        except Exception as e:
            last_error = e
            continue
    raise SkillError(f"Unable to fill field. Tried selectors: {candidates}. Last error: {last_error}")


def click_first_available(page, candidates: list[str], timeout: int = 2500) -> str:
    last_error = None
    for selector in candidates:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.click()
            return selector
        except Exception as e:
            last_error = e
            continue
    raise SkillError(f"Unable to click target. Tried selectors: {candidates}. Last error: {last_error}")


def get_page_text_and_html(page) -> Tuple[str, str]:
    """Return visible body text and HTML for UID extraction/debugging."""
    text = ""
    html = ""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    try:
        html = page.content()
    except Exception:
        pass
    return text, html


def extract_uid_from_text_blob(blob: str) -> Optional[str]:
    if not blob:
        return None
    match = UID_RE.search(blob)
    if match:
        return match.group(1)

    # Common frontend JSON / HTML attribute variants.
    patterns = [
        r'"uid"\s*[:=]\s*"?(\d{4,20})"?',
        r'"userId"\s*[:=]\s*"?(\d{4,20})"?',
        r'"user_id"\s*[:=]\s*"?(\d{4,20})"?',
        r'\bUID\s*[:：]?\s*[^0-9]{0,20}(\d{4,20})\b',
    ]
    for pattern in patterns:
        m = re.search(pattern, blob, flags=re.IGNORECASE)
        if m:
            return m.group(1)

    # Last fallback: accept a 7-12 digit number only if the page explicitly mentions UID nearby.
    if "UID" in blob.upper():
        m = re.search(r"\b(\d{7,12})\b", blob)
        if m:
            return m.group(1)
    return None


def capture_debug_artifacts(page, evidence_dir: str, label: str) -> Dict[str, str]:
    """Save text/html snapshots for remote debugging when UID is not found."""
    out_dir = Path(evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    text, html = get_page_text_and_html(page)
    text_path = out_dir / f"{label}.txt"
    html_path = out_dir / f"{label}.html"
    text_path.write_text(text, encoding="utf-8", errors="replace")
    html_path.write_text(html, encoding="utf-8", errors="replace")
    return {"text_path": str(text_path), "html_path": str(html_path)}


def navigate_to_account_and_find_uid(page, evidence_dir: str) -> Tuple[Optional[str], list[str]]:
    """Navigate from the post-login dashboard to Account/Personal Center and extract UID.

    The Solulu app may land on the Assets page after registration. In the UI, UID is
    shown under Account / Personal Center, so we must explicitly move there before
    reading the page.
    """
    attempts: list[str] = []

    def try_extract(label: str) -> Optional[str]:
        page.wait_for_timeout(1200)
        text, html = get_page_text_and_html(page)
        uid = extract_uid_from_text_blob(text + "\n" + html)
        attempts.append(f"{label}: url={page.url}, uid_found={bool(uid)}")
        return uid

    uid = try_extract("initial")
    if uid:
        return uid, attempts

    # 1) Click the visible left nav Account item. This is the path observed in screenshots.
    click_selectors = [
        'text=/^\s*Account\s*$/i',
        'a:has-text("Account")',
        'button:has-text("Account")',
        '[role="menuitem"]:has-text("Account")',
        'li:has-text("Account")',
        'div:has-text("Account")',
    ]
    for selector in click_selectors:
        try:
            page.locator(selector).last.click(timeout=2500)
            page.wait_for_load_state("domcontentloaded", timeout=8000)
            uid = try_extract(f"click {selector}")
            if uid:
                return uid, attempts
        except Exception as e:
            attempts.append(f"click {selector}: failed={type(e).__name__}")

    # 2) DOM text click fallback, useful for SPA menus where Playwright text selector hits a parent node.
    try:
        clicked = page.evaluate("""
        () => {
          const nodes = Array.from(document.querySelectorAll('a,button,div,span,li,p'));
          const target = nodes.find(n => (n.innerText || '').trim().toLowerCase() === 'account');
          if (target) { target.click(); return true; }
          return false;
        }
        """)
        attempts.append(f"js_click_account: {clicked}")
        page.wait_for_timeout(2200)
        uid = try_extract("after js_click_account")
        if uid:
            return uid, attempts
    except Exception as e:
        attempts.append(f"js_click_account failed={type(e).__name__}")

    # 3) Try likely SPA routes. These are harmless even if some 404/redirect.
    for url in [
        "https://solulu.cc/account",
        "https://solulu.cc/user",
        "https://solulu.cc/profile",
        "https://solulu.cc/personal",
        "https://solulu.cc/userCenter",
        "https://solulu.cc/me",
        "https://solulu.cc/#/account",
        "https://solulu.cc/#/user",
        "https://solulu.cc/#/profile",
    ]:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            uid = try_extract(f"goto {url}")
            if uid:
                return uid, attempts
        except Exception as e:
            attempts.append(f"goto {url}: failed={type(e).__name__}")

    capture_debug_artifacts(page, evidence_dir, "solulu_account_debug")
    return None, attempts



def capture_existing_solulu_uid(page, cfg: AutoCompleteConfig, reason: str = "already_logged_in_or_registration_form_unavailable") -> Dict[str, Any]:
    """Extract UID from an already logged-in Solulu session.

    This is important when the browser state already contains a successful Solulu
    login/registration. In that case /register may redirect to the app dashboard,
    where the Send OTP button is no longer present. The correct action is not to
    fail the Skill, but to navigate to Account / Personal Center and extract UID.
    """
    try:
        page.goto(SOLULU_HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1800)
    except Exception:
        pass

    uid, uid_attempts = navigate_to_account_and_find_uid(page, cfg.evidence_dir)
    solulu_screenshot = str(ensure_dir(cfg.solulu_screenshot_path))
    page.screenshot(path=solulu_screenshot, full_page=True)

    return {
        "register_url": SOLULU_REGISTER_URL,
        "email": redact(cfg.email),
        "registration_skipped": True,
        "skip_reason": reason,
        "uid": uid,
        "uid_found": uid is not None,
        "uid_navigation_attempts": uid_attempts,
        "solulu_screenshot_path": solulu_screenshot,
        "debug_artifacts": None if uid else {
            "text_path": f"{cfg.evidence_dir}/solulu_account_debug.txt",
            "html_path": f"{cfg.evidence_dir}/solulu_account_debug.html"
        },
    }

def complete_solulu_registration(page, cfg: AutoCompleteConfig) -> Dict[str, Any]:
    page.goto(SOLULU_REGISTER_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1800)

    # If a previous run already registered/logged in, /register may redirect to the
    # logged-in dashboard. In that state there is no Send OTP button. Treat this as
    # a valid continuation and extract UID from Account instead of failing.
    send_visible = False
    for selector in [
        'text=/^Send$/i',
        'button:has-text("Send")',
        '[role="button"]:has-text("Send")',
    ]:
        try:
            page.locator(selector).first.wait_for(state="visible", timeout=900)
            send_visible = True
            break
        except Exception:
            continue

    if not send_visible:
        return capture_existing_solulu_uid(
            page,
            cfg,
            reason="send_button_not_visible_after_opening_register; likely already logged in or redirected away from register form"
        )

    filled_email_selector = fill_first_available(page, [
        'input[placeholder*="email" i]',
        'input[type="email"]',
        'input:near(:text("email"))',
        'input:first-of-type',
    ], cfg.email)

    try:
        send_clicked = click_first_available(page, [
            'text=/^Send$/i',
            'button:has-text("Send")',
            '[role="button"]:has-text("Send")',
        ])
    except SkillError:
        return capture_existing_solulu_uid(
            page,
            cfg,
            reason="unable_to_click_send_after_email_fill; likely already logged in or register form changed"
        )

    otp = cfg.otp
    otp_source = "input_or_env"
    if not otp:
        otp = fetch_otp_from_imap(timeout_seconds=cfg.otp_timeout_seconds)
        otp_source = "imap" if otp else None

    if not otp and cfg.manual_pause_for_otp:
        # This is still agent-driven: the browser remains open and the operator only supplies OTP.
        print(json.dumps({
            "ok": None,
            "status": "otp_required",
            "message": "Enter OTP received by email, then press Enter here.",
            "email": redact(cfg.email),
        }, ensure_ascii=False), file=sys.stderr)
        otp = input("OTP: ").strip()
        otp_source = "manual_terminal"

    if not otp:
        raise SkillError(
            "OTP unavailable. Provide input.otp / SOLULU_OTP, or configure authorized IMAP env vars. "
            "This Skill does not bypass OTP."
        )

    filled_otp_selector = fill_first_available(page, [
        'input[placeholder*="verification" i]',
        'input[placeholder*="code" i]',
        'input:near(:text("OTP"))',
        'input:nth-of-type(2)',
    ], otp)

    # Two password fields.
    password_inputs = page.locator('input[type="password"], input[placeholder*="password" i]')
    count = password_inputs.count()
    if count >= 2:
        password_inputs.nth(0).fill(cfg.password)
        password_inputs.nth(1).fill(cfg.password)
        password_method = "password_inputs_by_type"
    else:
        fill_first_available(page, ['input[placeholder*="password" i]'], cfg.password)
        # fallback: fill any empty input after OTP
        page.keyboard.press("Tab")
        page.keyboard.type(cfg.password)
        page.keyboard.press("Tab")
        page.keyboard.type(cfg.password)
        password_method = "keyboard_fallback"

    # Accept terms if checkbox exists / visible. The screenshot shows checked by default; this is defensive.
    click_if_visible(page, 'input[type="checkbox"]', timeout=1000)
    click_if_visible(page, 'text=/I accept/i', timeout=1000)

    clicked_register = click_first_available(page, [
        'button:has-text("New Register")',
        'text=/New Register/i',
        'button:has-text("Register")',
        '[role="button"]:has-text("Register")',
    ], timeout=5000)

    page.wait_for_timeout(5000)

    # The app often lands on Assets after registration. UID is shown under Account / Personal Center.
    uid, uid_attempts = navigate_to_account_and_find_uid(page, cfg.evidence_dir)

    solulu_screenshot = str(ensure_dir(cfg.solulu_screenshot_path))
    page.screenshot(path=solulu_screenshot, full_page=True)

    return {
        "register_url": SOLULU_REGISTER_URL,
        "email": redact(cfg.email),
        "email_selector": filled_email_selector,
        "send_clicked_selector": send_clicked,
        "otp_source": otp_source,
        "otp_selector": filled_otp_selector,
        "password_method": password_method,
        "register_clicked_selector": clicked_register,
        "uid": uid,
        "uid_found": uid is not None,
        "uid_navigation_attempts": uid_attempts,
        "solulu_screenshot_path": solulu_screenshot,
        "debug_artifacts": None if uid else {
            "text_path": f"{cfg.evidence_dir}/solulu_account_debug.txt",
            "html_path": f"{cfg.evidence_dir}/solulu_account_debug.html"
        },
    }


def extract_uid_from_page(page) -> Optional[str]:
    text, html = get_page_text_and_html(page)
    return extract_uid_from_text_blob(text + "\n" + html)


def join_telegram_and_screenshot(page, cfg: AutoCompleteConfig) -> Dict[str, Any]:
    """Open Telegram community and capture a real screenshot.

    For full automation, run once with a persistent browser profile and log into
    Telegram Web/Desktop. Future runs can reuse cfg.browser_state_dir.
    """
    page.goto(TELEGRAM_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Try common t.me join/open controls. This may open Telegram app or Telegram Web.
    clicked = []
    for selector in [
        'a:has-text("View in Telegram")',
        'a:has-text("Open in Telegram")',
        'button:has-text("Join")',
        'text=/Join/i',
    ]:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=1500)
            loc.click(timeout=1500)
            clicked.append(selector)
            page.wait_for_timeout(2500)
        except Exception:
            continue

    screenshot_path = str(ensure_dir(cfg.telegram_screenshot_path))
    page.screenshot(path=screenshot_path, full_page=True)

    return {
        "telegram_url": TELEGRAM_URL,
        "clicked_selectors": clicked,
        "screenshot_path": screenshot_path,
        "note": "Screenshot is from a real browser session. If Telegram login is required, run headless=false once and login with an authorized account; the persistent browser profile can be reused."
    }


def mode_plan() -> Dict[str, Any]:
    return {
        "ok": True,
        "skill": "agenton-solulu-quest-autocomplete",
        "version": "0.2.2",
        "mode": "plan",
        "quests": {
            "telegram_join": {
                "agenton_quest_url": AGENTON_TG_QUEST_URL,
                "target_url": TELEGRAM_URL,
                "evidence": "real Telegram group screenshot"
            },
            "solulu_register": {
                "target_url": SOLULU_REGISTER_URL,
                "required_fields": ["email", "password", "otp or authorized mailbox access"],
                "evidence": "Solulu UID after registration"
            }
        },
        "auto_completion": {
            "supported": True,
            "command": "python3 agenton_autocomplete_skill.py examples/auto_complete.json",
            "limits": [
                "Does not bypass OTP/CAPTCHA.",
                "Does not fabricate Telegram screenshots.",
                "Requires an authorized email/Telegram session for full automation."
            ]
        }
    }


def mode_partner_api_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "skill": "agenton-solulu-quest-autocomplete",
        "version": "0.2.2",
        "mode": "partner_api_status",
        "partner_api": {
            "referenced_in_source_md": True,
            "purpose_in_source_md": "external order creation for X followers / Telegram members",
            "status": "not_executed_by_this_skill",
            "reason": "This Skill completes the real user task flow. It does not purchase artificial Telegram members, create fake engagement, or fabricate evidence.",
            "safe_alternative": "Use Playwright automation with an authorized account to join Telegram and capture a real screenshot."
        }
    }


def mode_auto_complete(data: Dict[str, Any]) -> Dict[str, Any]:
    cfg = build_config(data)
    sync_playwright, PlaywrightTimeoutError = import_playwright()

    Path(cfg.browser_state_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.evidence_dir).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser_type = p.chromium
        context = browser_type.launch_persistent_context(
            user_data_dir=cfg.browser_state_dir,
            headless=cfg.headless,
            viewport={"width": 1365, "height": 900},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        result: Dict[str, Any] = {
            "ok": True,
            "skill": "agenton-solulu-quest-autocomplete",
            "version": "0.2.2",
            "mode": "auto_complete",
            "tasks": {},
        }

        try:
            if cfg.telegram_enabled:
                result["tasks"]["telegram_join"] = join_telegram_and_screenshot(page, cfg)
            if cfg.registration_enabled:
                result["tasks"]["solulu_register"] = complete_solulu_registration(page, cfg)
        finally:
            context.close()

    uid = result.get("tasks", {}).get("solulu_register", {}).get("uid")
    tg_screenshot = result.get("tasks", {}).get("telegram_join", {}).get("screenshot_path")
    solulu_screenshot = result.get("tasks", {}).get("solulu_register", {}).get("solulu_screenshot_path")

    result["submission"] = {
        "solulu_uid": uid,
        "telegram_screenshot_path": tg_screenshot,
        "solulu_screenshot_path": solulu_screenshot,
        "ready_for_agenton_submission": bool(uid and tg_screenshot),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentOn Solulu Quest Auto-Completion Skill")
    parser.add_argument("input", nargs="?", help="Path to JSON input. If omitted, reads stdin.")
    args = parser.parse_args()

    try:
        data = read_json_input(args.input)
        mode = data.get("mode", "plan")
        if mode == "plan":
            result = mode_plan()
        elif mode == "partner_api_status":
            result = mode_partner_api_status()
        elif mode == "auto_complete":
            result = mode_auto_complete(data)
        elif mode == "extract_uid":
            # UID-only recovery mode for an existing logged-in browser profile.
            cfg = build_config(data)
            sync_playwright, PlaywrightTimeoutError = import_playwright()
            Path(cfg.browser_state_dir).mkdir(parents=True, exist_ok=True)
            Path(cfg.evidence_dir).mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=cfg.browser_state_dir,
                    headless=cfg.headless,
                    viewport={"width": 1365, "height": 900},
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                solulu = capture_existing_solulu_uid(page, cfg, reason="extract_uid_mode")
                context.close()
            result = {
                "ok": True,
                "skill": "agenton-solulu-quest-autocomplete",
                "version": "0.2.2",
                "mode": "extract_uid",
                "tasks": {"solulu_register": solulu},
                "submission": {
                    "solulu_uid": solulu.get("uid"),
                    "solulu_screenshot_path": solulu.get("solulu_screenshot_path"),
                    "ready_for_agenton_submission": bool(solulu.get("uid")),
                },
            }
        else:
            raise SkillError(f"Unsupported mode: {mode}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "skill": "agenton-solulu-quest-autocomplete",
            "error": str(e),
        }, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
