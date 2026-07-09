#!/usr/bin/env python3
"""Real-network smoke test for the Douyin download pipeline.

This exercises the SAME code path as `download-selected` over real sockets, so
you can prove the end-to-end pipeline (fetch -> mp4/cover/images -> index.csv)
works on your machine.

Modes:

  --self-test
      Spin up a local HTTP server, serve a synthetic aweme JSON + media bytes,
      seed a capture session, then run the real `download-selected` command
      against the local server. Needs no Douyin access — runs anywhere.

  --account "<profile-url-or-sec_uid>" [--latest N] [--cookie-file F]
      Attempt a real account via `download-user` (best-effort web extraction).
      Requires Douyin to be reachable; usually needs your login cookie. If the
      signed API blocks it, use capture mode instead.

  --session-id "<id>"
      Run `download-selected` for an existing capture session (the reliable
      path: capture-prepare -> scroll -> select, then this).

Exit code 0 = PASS, non-zero = FAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import douyin_downloader as dd  # noqa: E402

DOWNLOADER = str(SCRIPT_DIR / "douyin_downloader.py")


def _run_cli(runtime_dir: Path, args: list[str], extra_env: dict | None = None) -> tuple[int, dict]:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, DOWNLOADER, "--runtime-dir", str(runtime_dir), *args],
        text=True,
        capture_output=True,
        env=env,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "raw_stdout": proc.stdout, "stderr": proc.stderr}
    return proc.returncode, payload


# --------------------------------------------------------------------------
# self-test: local media server + real download-selected
# --------------------------------------------------------------------------


class _MediaHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith(".mp4"):
            ctype, body = "video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"FAKE-VIDEO" * 64
        else:
            ctype, body = "image/jpeg", b"\xff\xd8\xff\xe0" + b"FAKE-IMG" * 32
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence
        return


def _synthetic_records(host: str) -> list[dict]:
    payload = {
        "aweme_list": [
            {
                "aweme_id": "9000000000000000001",
                "desc": "smoke video A",
                "create_time": 1730000000,
                "author": {"nickname": "smoke-account"},
                "video": {
                    "cover": {"url_list": [f"{host}/coverA.jpg"]},
                    "play_addr": {"url_list": [f"{host}/videoA_default.mp4"]},
                    "bit_rate": [
                        {"bit_rate": 2000000, "play_addr": {"url_list": [f"{host}/videoA_hd.mp4"]}},
                        {"bit_rate": 800000, "play_addr": {"url_list": [f"{host}/videoA_low.mp4"]}},
                    ],
                },
                "statistics": {"digg_count": 10},
            },
            {
                "aweme_id": "9000000000000000002",
                "desc": "smoke gallery B",
                "create_time": 1731000000,
                "aweme_type": 68,
                "author": {"nickname": "smoke-account"},
                "images": [{"url_list": [f"{host}/imgB1.jpg"]}, {"url_list": [f"{host}/imgB2.jpg"]}],
                "video": {"cover": {"url_list": [f"{host}/coverB.jpg"]}},
            },
        ]
    }
    return dd.parse_aweme_payload(payload, source="post")


def run_self_test() -> int:
    # Ensure urllib talks to 127.0.0.1 directly, not through an egress proxy.
    os.environ["no_proxy"] = "127.0.0.1,localhost," + os.environ.get("no_proxy", "")
    os.environ["NO_PROXY"] = os.environ["no_proxy"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
    host = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    tmp = Path(tempfile.mkdtemp(prefix="douyin-smoke-"))
    runtime = tmp / "runtime"
    out = tmp / "out"
    try:
        records = _synthetic_records(host)
        session = dd.create_session(runtime, "smoke-account", "post")
        sid = session["session_id"]
        dd.append_captured(runtime, sid, records)

        print(f"[self-test] local media server: {host}")
        print(f"[self-test] session: {sid}  captured: {len(records)}")

        code, result = _run_cli(
            runtime,
            ["download-selected", "--session-id", sid, "--output-dir", str(out)],
            extra_env={"no_proxy": os.environ["no_proxy"], "NO_PROXY": os.environ["NO_PROXY"]},
        )
        print("[self-test] download-selected result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        ok = (
            code == 0
            and result.get("ok")
            and result.get("success_count") == 2
            and result.get("failure_count") == 0
        )
        # verify real files on disk
        video = list((out / "videos").glob("001-*.mp4"))
        img1 = out / "images" / "002" / "01.jpg"
        index = out / "index.csv"
        ok = ok and len(video) == 1 and img1.exists() and index.exists()
        # verify best-quality url was actually used
        used_hd = video and video[0].read_bytes().startswith(b"\x00\x00\x00\x18ftyp")

        # independent validator
        validator = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "validate_outputs.py"), str(out)],
            text=True, capture_output=True,
        )
        vok = validator.returncode == 0

        print(f"[self-test] files: video={len(video)} image1={img1.exists()} index={index.exists()}")
        print(f"[self-test] validate_outputs ok={vok}")
        if ok and used_hd and vok:
            print("SELF-TEST: PASS ✅  (real-socket download pipeline works end-to-end)")
            return 0
        print("SELF-TEST: FAIL ❌")
        return 1
    finally:
        server.shutdown()
        # leave tmp for inspection on failure; comment out to keep
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_account(url: str, latest: int, cookie_file: str) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="douyin-smoke-acct-"))
    runtime = tmp / "runtime"
    out = tmp / "out"
    args = ["download-user", url, "--latest", str(latest), "--output-dir", str(out)]
    if cookie_file:
        args += ["--cookie-file", cookie_file]
    code, result = _run_cli(runtime, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if code == 0 and result.get("ok"):
        print(f"ACCOUNT SMOKE: PASS ✅  files in {out}")
        return 0
    print("ACCOUNT SMOKE: could not fetch via web extraction — use capture mode (see next_step).")
    return 1


def run_session(session_id: str, latest: int, output_dir: str, cookie_file: str) -> int:
    runtime = dd.runtime_dir("")
    args = ["download-selected", "--session-id", session_id]
    if latest:
        args += ["--latest", str(latest)]
    if output_dir:
        args += ["--output-dir", output_dir]
    if cookie_file:
        args += ["--cookie-file", cookie_file]
    code, result = _run_cli(runtime, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if code == 0 and result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-network smoke test for the Douyin downloader")
    parser.add_argument("--self-test", action="store_true", help="run the local end-to-end pipeline check")
    parser.add_argument("--account", default="", help="profile URL or sec_uid to try (best-effort)")
    parser.add_argument("--session-id", default="", help="existing capture session to download")
    parser.add_argument("--latest", type=int, default=10)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--cookie-file", default="")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if args.account:
        return run_account(args.account, args.latest, args.cookie_file)
    if args.session_id:
        return run_session(args.session_id, args.latest, args.output_dir, args.cookie_file)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
