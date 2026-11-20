from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from report_artifacts import parse_report_date, report_pdf_ready


ROOT = Path(__file__).resolve().parent.parent
EMAIL_CONFIG = ROOT / "config" / "email_auth.json"
REPORT_DIR = ROOT / "synthesis" / "daily_reports"
SENT_DIR = ROOT / "system" / "email_sent"
LOCK_DIR = ROOT / "system" / "locks"
LOCK_STALE_SECONDS = 2 * 60 * 60


def load_config() -> dict:
    if not EMAIL_CONFIG.exists():
        raise FileNotFoundError(
            f"Missing {EMAIL_CONFIG}. Copy config/email_auth.example.json "
            "to config/email_auth.json and fill the 163 SMTP authorization code."
        )
    config = json.loads(EMAIL_CONFIG.read_text(encoding="utf-8"))
    required = ["smtp_host", "smtp_port", "username", "password", "from_email", "to_emails"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"Missing email config fields: {', '.join(missing)}")
    if "replace-with" in str(config.get("password", "")):
        raise ValueError("Email config still contains the example SMTP password placeholder.")
    return config


def wait_until_today(clock_text: str) -> None:
    if not clock_text:
        return
    target_time = datetime.strptime(clock_text, "%H:%M").time()
    now = datetime.now()
    target = datetime.combine(now.date(), target_time)
    if now > target + timedelta(minutes=1):
        return
    while datetime.now() < target:
        remaining = (target - datetime.now()).total_seconds()
        time.sleep(min(max(remaining, 0), 30))


def report_artifact_for(date_text: str) -> tuple[Path, str]:
    report_dir = REPORT_DIR / date_text
    pdf_path = report_dir / "report.pdf"
    legacy_md_path = REPORT_DIR / f"{date_text}.md"
    if report_pdf_ready(report_dir):
        return pdf_path, "pdf"
    if not report_dir.exists() and legacy_md_path.is_file():
        return legacy_md_path, "markdown"
    return pdf_path, "missing"


def sent_marker_for(date_text: str) -> Path:
    return SENT_DIR / f"{date_text}.sent"


def send_lock_for(date_text: str) -> Path:
    return LOCK_DIR / f"send-daily-report-{date_text}.lock"


def mark_sent(date_text: str, report_path: Path, config: dict) -> None:
    SENT_DIR.mkdir(parents=True, exist_ok=True)
    sent_marker_for(date_text).write_text(
        "\n".join(
            [
                f"sent_at={datetime.now().isoformat(timespec='seconds')}",
                f"report={report_path}",
                f"to={', '.join(config['to_emails'])}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def acquire_send_lock(date_text: str) -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = send_lock_for(date_text)
    if lock_path.exists():
        age_seconds = time.time() - lock_path.stat().st_mtime
        if age_seconds < LOCK_STALE_SECONDS:
            raise RuntimeError(f"Another send appears to be in progress: {lock_path}")
        lock_path.unlink()
    try:
        with lock_path.open("x", encoding="utf-8") as lock_file:
            lock_file.write(
                f"pid={os.getpid()}\nstarted_at={datetime.now().isoformat(timespec='seconds')}\n"
            )
    except FileExistsError as exc:
        raise RuntimeError(f"Another send appears to be in progress: {lock_path}") from exc
    return lock_path


def release_send_lock(lock_path: Path | None) -> None:
    if lock_path and lock_path.exists():
        lock_path.unlink()


def build_message(config: dict, report_path: Path, report_kind: str, date_text: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"点子发芽个人认知雷达报告 {date_text}"
    message["From"] = config["from_email"]
    message["To"] = ", ".join(config["to_emails"])
    if report_kind == "pdf":
        message.set_content(
            f"点子发芽个人认知雷达报告 {date_text} 已生成，PDF 见附件。\n",
            subtype="plain",
            charset="utf-8",
        )
        message.add_attachment(
            report_path.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=f"idea-sprout-radar-{date_text}.pdf",
        )
    else:
        content = report_path.read_text(encoding="utf-8")
        message.set_content(content, subtype="plain", charset="utf-8")
        message.add_attachment(
            content.encode("utf-8"),
            maintype="text",
            subtype="markdown",
            filename=report_path.name,
        )
    return message


def send_message(config: dict, message: EmailMessage) -> None:
    host = config["smtp_host"]
    port = int(config["smtp_port"])
    timeout = int(config.get("timeout_seconds", 30))
    if config.get("use_ssl", True):
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the Idea Sprout daily report by email.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--wait-until", default="", help="Local HH:MM time to wait until before sending.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-ready", action="store_true", help="Return success only for a quality-approved PDF.")
    parser.add_argument("--force", action="store_true", help="Send even if this date has a sent marker.")
    args = parser.parse_args()
    try:
        parse_report_date(args.date)
    except ValueError as exc:
        parser.error(str(exc))
    if args.check_ready:
        return 0 if report_pdf_ready(REPORT_DIR / args.date) else 1

    lock_path = None
    try:
        report_path, report_kind = report_artifact_for(args.date)
        marker = sent_marker_for(args.date)
        if report_kind == "missing":
            raise FileNotFoundError(
                f"No quality-approved report. Expected {REPORT_DIR / args.date / 'report.pdf'} "
                f"or legacy {REPORT_DIR / (args.date + '.md')}"
            )
        if marker.exists() and not args.force:
            print(f"Already sent {report_path}; marker exists at {marker}.")
            return 0
        config = load_config()
        wait_until_today(args.wait_until)
        message = build_message(config, report_path, report_kind, args.date)
        if args.dry_run:
            print(f"Dry run OK. Would send {report_path} to {', '.join(config['to_emails'])}.")
            return 0
        lock_path = acquire_send_lock(args.date)
        if marker.exists() and not args.force:
            print(f"Already sent {report_path}; marker exists at {marker}.")
            return 0
        send_message(config, message)
        mark_sent(args.date, report_path, config)
        print(f"Sent {report_path} to {', '.join(config['to_emails'])}.")
        return 0
    except Exception as exc:
        print(f"Failed to send daily report email: {exc}", file=sys.stderr)
        return 1
    finally:
        release_send_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
