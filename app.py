from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
CONFIG_DIR = ROOT / "config"
AUTH_CONFIG = CONFIG_DIR / "local_auth.json"
AUTH_EXAMPLE = CONFIG_DIR / "local_auth.example.json"
COOKIE_NAME = "idea_sprout_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_BODY_BYTES = 128 * 1024
GIT_TIMEOUT_SECONDS = 60

PRIMARY_DIRS = {
    "inbox": ROOT / "inbox",
    "knowledge": ROOT / "knowledge",
    "daily_reports": ROOT / "synthesis" / "daily_reports",
    "idea_seeds": ROOT / "synthesis" / "idea_seeds",
    "system": ROOT / "system",
}

LEGACY_DIRS = {
    "knowledge": ROOT / "library" / "nodes",
    "daily_reports": ROOT / "reports" / "daily",
    "idea_seeds": ROOT / "library" / "seeds",
}


def ensure_runtime_files() -> None:
    for path in PRIMARY_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_CONFIG.exists():
        config = {
            "password": "change-me",
            "session_secret": secrets.token_urlsafe(32),
            "note": "本地自用配置。请修改 password；也可以改用 password_sha256。",
        }
        AUTH_CONFIG.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def load_auth_config() -> dict:
    ensure_runtime_files()
    config = json.loads(AUTH_CONFIG.read_text(encoding="utf-8"))
    changed = False
    if not config.get("session_secret"):
        config["session_secret"] = secrets.token_urlsafe(32)
        changed = True
    if changed:
        AUTH_CONFIG.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return config


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def sign_payload(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return b64url_encode(digest.digest())


def make_session_cookie() -> str:
    config = load_auth_config()
    now = int(time.time())
    payload = b64url_encode(
        json.dumps({"iat": now, "exp": now + SESSION_SECONDS}, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    signature = sign_payload(payload, config["session_secret"])
    token = f"{payload}.{signature}"
    return (
        f"{COOKIE_NAME}={token}; Max-Age={SESSION_SECONDS}; "
        "Path=/; HttpOnly; SameSite=Lax"
    )


def clear_session_cookie() -> str:
    return f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"


def parse_cookie(header: str | None) -> SimpleCookie:
    cookie = SimpleCookie()
    if header:
        cookie.load(header)
    return cookie


def is_valid_session(cookie_header: str | None) -> bool:
    cookie = parse_cookie(cookie_header)
    if COOKIE_NAME not in cookie:
        return False
    token = cookie[COOKIE_NAME].value
    if "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    config = load_auth_config()
    expected = sign_payload(payload, config["session_secret"])
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        data = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    return int(data.get("exp", 0)) > int(time.time())


def password_matches(candidate: str) -> bool:
    config = load_auth_config()
    if "password_sha256" in config and config["password_sha256"]:
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, str(config["password_sha256"]))
    return hmac.compare_digest(candidate, str(config.get("password", "")))


def first_heading(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except OSError:
        pass
    return path.stem


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def list_markdown_files(paths: list[Path], limit: int | None = None) -> list[dict]:
    items: list[dict] = []
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if not path.is_file():
                continue
            stat = path.stat()
            items.append(
                {
                    "title": first_heading(path),
                    "path": relative_path(path),
                    "directory": relative_path(base),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                }
            )
    items.sort(key=lambda item: item["modified"], reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def allowed_file(path_text: str, kind: str) -> Path | None:
    allowed_dirs = {
        "report": [PRIMARY_DIRS["daily_reports"], LEGACY_DIRS["daily_reports"]],
        "knowledge": [PRIMARY_DIRS["knowledge"], LEGACY_DIRS["knowledge"]],
        "seed": [PRIMARY_DIRS["idea_seeds"], LEGACY_DIRS["idea_seeds"]],
    }
    if kind not in allowed_dirs:
        return None
    candidate = (ROOT / path_text).resolve()
    if candidate.suffix.lower() != ".md" or not candidate.is_file():
        return None
    for base in allowed_dirs[kind]:
        try:
            candidate.relative_to(base.resolve())
            return candidate
        except ValueError:
            continue
    return None


def today_inbox_path() -> Path:
    return PRIMARY_DIRS["inbox"] / f"{datetime.now().strftime('%Y-%m-%d')}.md"


def create_inbox_if_needed(path: Path) -> None:
    if path.exists():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    template = ROOT / "templates" / "daily-input.md"
    if template.exists():
        content = template.read_text(encoding="utf-8").replace("YYYY-MM-DD", today)
    else:
        content = (
            f"# 每日输入 {today}\n\n"
            "> 本文件由网页端自动维护。日常输入只通过网页提交：关键词、上下文、权重。\n\n"
            "## 网页输入记录\n"
        )
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def run_git(args: list[str], timeout: int = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def short_command_output(process: subprocess.CompletedProcess, limit: int = 500) -> str:
    output = "\n".join(
        part.strip()
        for part in [process.stdout, process.stderr]
        if part and part.strip()
    )
    if len(output) > limit:
        return output[:limit].rstrip() + "..."
    return output


def auto_git_sync(path: Path) -> dict:
    disabled = os.environ.get("IDEA_SPROUT_AUTO_GIT_SYNC", "").lower() in {
        "0",
        "false",
        "no",
        "off",
    }
    relative = relative_path(path)
    if disabled:
        return {
            "enabled": False,
            "committed": False,
            "pushed": False,
            "message": "Auto git sync is disabled by IDEA_SPROUT_AUTO_GIT_SYNC.",
        }

    try:
        inside = run_git(["rev-parse", "--is-inside-work-tree"])
        if inside.returncode != 0:
            return {
                "enabled": False,
                "committed": False,
                "pushed": False,
                "message": "This folder is not a git repository.",
            }

        branch = run_git(["branch", "--show-current"])
        branch_name = branch.stdout.strip() or "main"
        remote = run_git(["remote", "get-url", "origin"])
        if remote.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": "No git remote named origin is configured.",
            }

        status = run_git(["status", "--porcelain", "--", relative])
        if status.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": short_command_output(status),
            }
        if not status.stdout.strip():
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": "No git changes detected for the inbox file.",
            }

        added = run_git(["add", "--", relative])
        if added.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": short_command_output(added),
            }

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit = run_git(["commit", "-m", f"Auto sync inbox {stamp}", "--", relative])
        if commit.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": short_command_output(commit),
            }

        push = run_git(["push", "origin", branch_name], timeout=120)
        if push.returncode != 0:
            return {
                "enabled": True,
                "committed": True,
                "pushed": False,
                "message": short_command_output(push),
            }

        commit_hash = run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        return {
            "enabled": True,
            "committed": True,
            "pushed": True,
            "commit": commit_hash,
            "branch": branch_name,
            "message": "Committed and pushed to GitHub.",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "enabled": True,
            "committed": False,
            "pushed": False,
            "message": str(exc),
        }


def append_keywords(payload: dict) -> dict:
    raw_keywords = str(payload.get("keywords", ""))
    keywords = []
    for line in raw_keywords.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if cleaned:
            keywords.append(cleaned)
    context = str(payload.get("context", "")).strip()
    weight = str(payload.get("weight", "")).strip()
    if weight and weight not in {"1", "2", "3", "4", "5"}:
        raise ValueError("权重只能为空或 1-5。")
    if not keywords:
        raise ValueError("请至少输入一个关键词。")

    inbox_path = today_inbox_path()
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    create_inbox_if_needed(inbox_path)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["", f"### 网页输入 {now}", ""]
    for keyword in keywords:
        lines.append(f"- 关键词：{keyword}")
        lines.append(f"  - 上下文：{context or '未填写'}")
        lines.append(f"  - 权重：{weight or '未填写'}")
    lines.append("")

    with inbox_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))

    sync = auto_git_sync(inbox_path)
    return {"path": relative_path(inbox_path), "count": len(keywords), "sync": sync}


def primary_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        return None
    return None


def lan_ip_candidates() -> list[str]:
    candidates: list[str] = []
    primary = primary_lan_ip()
    if primary:
        candidates.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in candidates:
                candidates.append(ip)
    except OSError:
        pass
    return candidates


def print_startup_urls(host: str, port: int) -> None:
    print("点子发芽网页已启动", flush=True)
    print(f"电脑本机访问: http://127.0.0.1:{port}", flush=True)
    if host in {"0.0.0.0", ""}:
        lan_ips = lan_ip_candidates()
        if lan_ips:
            print("手机访问链接（手机和电脑需在同一 Wi-Fi / 局域网）：", flush=True)
            for ip in lan_ips:
                print(f"  http://{ip}:{port}", flush=True)
        else:
            print("未能自动识别局域网 IP。可在 Windows 网络设置中查看本机 IPv4 地址。", flush=True)
        print("如果电脑正在使用 VPN，请确认 VPN 允许局域网 / LAN 访问。", flush=True)
        print("如果当前是校园网 / WPA2-Enterprise Wi-Fi，手机打不开时请优先尝试手机热点或电脑移动热点。", flush=True)
    else:
        print(f"当前仅监听: http://{host}:{port}", flush=True)
        print("如需手机访问，请使用 IDEA_SPROUT_HOST=0.0.0.0 启动。", flush=True)
    print(f"本地密码配置: {AUTH_CONFIG}", flush=True)


class IdeaSproutHandler(BaseHTTPRequestHandler):
    server_version = "IdeaSproutLocal/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_static_file(WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            static_path = (WEB_DIR / "static" / path.removeprefix("/static/")).resolve()
            if WEB_DIR.resolve() in static_path.parents:
                self.send_static_file(static_path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path == "/api/auth/status":
            self.send_json({"authenticated": self.authenticated()})
            return
        if path == "/api/overview":
            if not self.require_auth():
                return
            self.send_json(
                {
                    "reports": list_markdown_files(
                        [PRIMARY_DIRS["daily_reports"], LEGACY_DIRS["daily_reports"]],
                        limit=8,
                    ),
                    "knowledge": list_markdown_files(
                        [PRIMARY_DIRS["knowledge"], LEGACY_DIRS["knowledge"]]
                    ),
                    "seeds": list_markdown_files(
                        [PRIMARY_DIRS["idea_seeds"], LEGACY_DIRS["idea_seeds"]]
                    ),
                }
            )
            return
        if path == "/api/file":
            if not self.require_auth():
                return
            query = urllib.parse.parse_qs(parsed.query)
            kind = query.get("kind", [""])[0]
            file_path = query.get("path", [""])[0]
            target = allowed_file(file_path, kind)
            if not target:
                self.send_json({"error": "文件不存在或路径不允许。"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(
                {
                    "title": first_heading(target),
                    "path": relative_path(target),
                    "content": target.read_text(encoding="utf-8"),
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/login":
            payload = self.read_json_body()
            if payload is None:
                return
            if password_matches(str(payload.get("password", ""))):
                self.send_json(
                    {"authenticated": True, "expiresInDays": 30},
                    headers={"Set-Cookie": make_session_cookie()},
                )
            else:
                self.send_json({"error": "密码不正确。"}, HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path == "/api/logout":
            self.send_json(
                {"authenticated": False},
                headers={"Set-Cookie": clear_session_cookie()},
            )
            return
        if parsed.path == "/api/keywords":
            if not self.require_auth():
                return
            payload = self.read_json_body()
            if payload is None:
                return
            try:
                result = append_keywords(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(result)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def authenticated(self) -> bool:
        return is_valid_session(self.headers.get("Cookie"))

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_json({"error": "需要登录。"}, HTTPStatus.UNAUTHORIZED)
        return False

    def read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            self.send_json({"error": "请求内容过大。"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "JSON 格式不正确。"}, HTTPStatus.BAD_REQUEST)
            return None

    def send_json(
        self,
        data: dict,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def send_static_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(path.name)
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}", flush=True)


def run() -> None:
    ensure_runtime_files()
    host = os.environ.get("IDEA_SPROUT_HOST", "0.0.0.0")
    port = int(os.environ.get("IDEA_SPROUT_PORT", "3000"))
    server = ThreadingHTTPServer((host, port), IdeaSproutHandler)
    print_startup_urls(host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
