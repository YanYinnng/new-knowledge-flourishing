"""Shared validation for dated report entry points and published PDFs."""

import hashlib
import json
from datetime import date
from pathlib import Path


def parse_report_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return value
    except ValueError:
        pass
    raise ValueError("--date must be a valid date in YYYY-MM-DD format.")


def report_fingerprint(report_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("report_brief.json", "report.tex"):
        text = (report_dir / name).read_text(encoding="utf-8")
        hashed = hashlib.sha256(text.encode("utf-8")).digest()
        digest.update(hashed)
    return digest.hexdigest()


def report_pdf_ready(report_dir: Path) -> bool:
    try:
        pdf = report_dir / "report.pdf"
        quality_path = report_dir / "quality_check.json"
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        ready = (
            isinstance(quality, dict)
            and quality.get("passed") is True
            and quality.get("pdf_ready", True) is True
            and pdf.stat().st_size > 0
            and pdf.is_file()
        )
        if not ready:
            return False
        fingerprint = quality.get("report_fingerprint")
        return not fingerprint or fingerprint == report_fingerprint(report_dir)
    except (OSError, ValueError):
        return False
