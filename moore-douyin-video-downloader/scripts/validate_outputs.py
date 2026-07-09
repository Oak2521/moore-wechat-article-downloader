#!/usr/bin/env python3
"""Validate a Douyin download output directory.

Checks that index.csv exists, that referenced files are present, and reports
per-item status. Exit code is non-zero when the directory is malformed.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def validate(output_dir: str) -> dict:
    base = Path(output_dir).expanduser()
    index = base / "index.csv"
    if not base.exists():
        return {"ok": False, "error": f"directory not found: {base}"}
    if not index.exists():
        return {"ok": False, "error": f"index.csv not found in {base}"}

    with index.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    present = 0
    missing = []
    success_rows = 0
    for row in rows:
        if row.get("status") == "success":
            success_rows += 1
        rel = (row.get("file") or "").strip()
        if not rel:
            continue
        target = base / rel
        if target.exists():
            present += 1
        else:
            missing.append(rel)

    return {
        "ok": len(missing) == 0,
        "dir": str(base),
        "rows": len(rows),
        "success_rows": success_rows,
        "files_present": present,
        "files_missing": missing,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: validate_outputs.py <dir>"}, ensure_ascii=False))
        return 1
    result = validate(argv[0])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
