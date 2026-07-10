#!/usr/bin/env python3
"""Read-only environment preflight for the WeChat article downloader.

Prints a single JSON object: {"ok": bool, "checks": [...], "next_step": "..."}
and exits 0 when a usable path exists, 1 when a blocking issue is present.

Run this FIRST (SKILL.md makes it the mandatory first step) so the assistant
can branch deterministically instead of guessing the environment. It never
changes anything — no proxy edits, no installs, no network side effects unless
--check-network is passed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import wechat_downloader as wd  # noqa: E402
import wechat_exporter as we  # noqa: E402


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _python_check() -> dict:
    ok = sys.version_info >= (3, 10)
    return _check("python", ok, f"{sys.version_info.major}.{sys.version_info.minor} (need >= 3.10)")


def _downloads_check() -> dict:
    target = wd.DEFAULT_DELIVERY_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target, delete=True):
            pass
        return _check("downloads_writable", True, str(target))
    except Exception as exc:  # noqa: BLE001
        return _check("downloads_writable", False, f"{target}: {exc}")


def _mitmdump_check() -> dict:
    path = shutil.which("mitmdump")
    return _check("mitmdump", bool(path), path or f"proxy history mode needs it — {wd.mitmproxy_install_hint()}")


def _cert_check() -> dict:
    mitm_dir = Path.home() / ".mitmproxy"
    files = [mitm_dir / n for n in ("mitmproxy-ca-cert.pem", "mitmproxy-ca-cert.cer", "mitmproxy-ca-cert.p12")]
    present = [str(f) for f in files if f.exists()]
    return _check(
        "mitmproxy_cert",
        bool(present),
        (", ".join(present) if present else "no cert yet — only needed for proxy history mode"),
    )


def _credential_store_check() -> dict:
    if we.keychain_available():
        return _check("credential_store", True, "macOS Keychain")
    if we.dpapi_available():
        return _check("credential_store", True, "Windows DPAPI")
    return _check("credential_store", True, "no OS secret store — use --allow-plain-auth-key for exporter mode")


def _exporter_db_check() -> dict:
    db_path = wd.APP_DIR / "exporter.sqlite"
    return _check("exporter_db", True, str(db_path) + (" (exists)" if db_path.exists() else " (not created yet)"))


def _system_proxy_check() -> dict:
    try:
        if wd.IS_WINDOWS:
            state = wd.get_windows_proxy_state()
            return _check("system_proxy", True, f"enabled={bool(state.get('enabled'))} server={state.get('server','') or '(none)'}")
        if wd.IS_MACOS:
            svc = wd.choose_network_service("")
            web = wd.get_network_proxy_state(svc).get("web", {})
            return _check("system_proxy", True, f"service={svc} enabled={web.get('enabled_bool')} server={web.get('server','')}")
        return _check("system_proxy", True, "linux — set/clear the proxy manually (127.0.0.1:8899) for proxy mode")
    except Exception as exc:  # noqa: BLE001
        return _check("system_proxy", True, f"unavailable: {exc}")


def _network_check() -> dict:
    try:
        req = urllib.request.Request("https://mp.weixin.qq.com/", method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _check("network_weixin", True, f"HTTP {resp.status}")
    except Exception as exc:  # noqa: BLE001
        return _check("network_weixin", False, f"unreachable: {exc}")


def run(check_network: bool = False) -> dict:
    checks = [
        _python_check(),
        _downloads_check(),
        _mitmdump_check(),
        _cert_check(),
        _credential_store_check(),
        _exporter_db_check(),
        _system_proxy_check(),
    ]
    if check_network:
        checks.append(_network_check())

    by = {c["name"]: c for c in checks}
    # URL download and exporter mode work with just python + a writable output
    # dir; mitmproxy is only needed for the optional proxy history mode.
    ok = by["python"]["ok"] and by["downloads_writable"]["ok"]

    if not by["python"]["ok"]:
        next_step = "Upgrade to Python 3.10+."
    elif not by["downloads_writable"]["ok"]:
        next_step = "Fix write access to the downloads directory (or pass --output-dir)."
    elif not by["mitmdump"]["ok"]:
        next_step = f"Ready for URL + Exporter modes. Proxy history mode additionally needs mitmproxy ({wd.mitmproxy_install_hint()})."
    else:
        next_step = "Ready. Pick a mode: URL download, Exporter (scan-login), or proxy history."

    return {"ok": ok, "platform": sys.platform, "checks": checks, "next_step": next_step}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only environment preflight for the WeChat article downloader")
    parser.add_argument("--check-network", action="store_true", help="also probe mp.weixin.qq.com reachability")
    args = parser.parse_args(argv)
    result = run(args.check_network)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
