#!/usr/bin/env python3
"""Offline contract test for the WeChat doctor preflight."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402


class DoctorTests(unittest.TestCase):
    def test_run_returns_wellformed_result(self) -> None:
        result = doctor.run(check_network=False)
        self.assertIn("ok", result)
        self.assertIsInstance(result["ok"], bool)
        self.assertIsInstance(result["checks"], list)
        self.assertTrue(result["next_step"])
        names = {c["name"] for c in result["checks"]}
        self.assertIn("python", names)
        self.assertIn("credential_store", names)
        for check in result["checks"]:
            self.assertEqual(set(check), {"name", "ok", "detail"})
            self.assertIsInstance(check["ok"], bool)

    def test_cli_prints_json_and_exit_code_matches_ok(self) -> None:
        env = dict(os.environ, MOORE_WECHAT_EXPORTER_DISABLE_KEYCHAIN="1")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "doctor.py")],
            text=True,
            capture_output=True,
            env=env,
        )
        payload = json.loads(proc.stdout)  # must be a single valid JSON object
        self.assertEqual(proc.returncode, 0 if payload["ok"] else 1)


if __name__ == "__main__":
    unittest.main()
