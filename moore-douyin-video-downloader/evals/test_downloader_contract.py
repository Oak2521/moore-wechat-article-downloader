#!/usr/bin/env python3
"""Offline contract tests for the Douyin downloader.

No real network or Douyin client is exercised. These validate the pure logic
(parsing, HD/no-watermark selection, image posts, endpoint matching, scrubbing,
the selection contract, capture dedupe, index.csv) and the download pipeline
with HTTP faked out.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals" / "fixtures" / "aweme_post_fixture.json"
sys.path.insert(0, str(ROOT / "scripts"))

import douyin_downloader as dd  # noqa: E402


class DownloaderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="moore-douyin-test-"))
        with FIXTURE.open("r", encoding="utf-8") as fh:
            self.payload = json.load(fh)
        self.records = dd.parse_aweme_payload(self.payload, source="post")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- parsing ---

    def test_parse_returns_all_records(self) -> None:
        self.assertEqual(len(self.records), 3)
        self.assertEqual([r["aweme_id"] for r in self.records], [
            "7300000000000000001", "7300000000000000002", "7300000000000000003",
        ])

    def test_best_video_url_prefers_highest_bitrate(self) -> None:
        rec = self.records[0]
        self.assertEqual(rec["video_url"], "https://v.example.com/play1_hd.mp4")
        # ordered candidates: hd (2M), mid (1M), low (0.5M), then default play_addr
        self.assertEqual(
            rec["video_candidates"],
            [
                "https://v.example.com/play1_hd.mp4",
                "https://v.example.com/play1_mid.mp4",
                "https://v.example.com/play1_low.mp4",
                "https://v.example.com/play1_default.mp4",
            ],
        )

    def test_quality_source_uses_plain_play_addr(self) -> None:
        rec = dd.parse_aweme_payload(self.payload, source="post", quality="source")[0]
        self.assertEqual(rec["video_url"], "https://v.example.com/play1_default.mp4")

    def test_image_post_detected(self) -> None:
        rec = self.records[2]
        self.assertEqual(rec["aweme_type"], "image")
        self.assertEqual(len(rec["image_urls"]), 2)
        self.assertEqual(rec["video_url"], "")

    def test_create_date_populated(self) -> None:
        expected = dt.datetime.fromtimestamp(1730000000, dt.timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(self.records[0]["create_date"], expected)

    def test_statistics_normalized(self) -> None:
        self.assertEqual(self.records[0]["statistics"]["digg_count"], 1234)
        self.assertEqual(self.records[0]["statistics"]["collect_count"], 89)

    def test_share_url_is_clean(self) -> None:
        self.assertEqual(self.records[0]["share_url"], "https://www.douyin.com/video/7300000000000000001")

    # --- endpoint matching ---

    def test_endpoint_matching(self) -> None:
        self.assertEqual(dd.match_endpoint("https://www.douyin.com/aweme/v1/web/aweme/post/?x=1"), "post")
        self.assertEqual(dd.match_endpoint("https://www.douyin.com/aweme/v1/web/aweme/favorite/?x=1"), "favorite")
        self.assertEqual(dd.match_endpoint("https://www.douyin.com/aweme/v1/web/aweme/listcollection/"), "collection")
        self.assertEqual(dd.match_endpoint("https://www.douyin.com/aweme/v1/web/mix/aweme/"), "mix")
        self.assertIsNone(dd.match_endpoint("https://www.douyin.com/web/anything/else"))

    # --- scrubbing ---

    def test_scrub_removes_signature_keeps_normal(self) -> None:
        url = "https://v.example.com/play2_default.mp4?a_bogus=SECRETSIG&normal=1"
        scrubbed = dd.scrub_url(url)
        self.assertNotIn("a_bogus", scrubbed)
        self.assertNotIn("SECRETSIG", scrubbed)
        self.assertIn("normal=1", scrubbed)

    # --- selection contract ---

    def test_selection_latest(self) -> None:
        self.assertEqual(len(dd.apply_selection(self.records, latest=2)), 2)

    def test_selection_indices_and_range(self) -> None:
        picked = dd.apply_selection(self.records, indices="1,3")
        self.assertEqual([r["aweme_id"] for r in picked], ["7300000000000000001", "7300000000000000003"])
        picked = dd.apply_selection(self.records, ranges="2-3")
        self.assertEqual([r["aweme_id"] for r in picked], ["7300000000000000002", "7300000000000000003"])

    def test_selection_contains(self) -> None:
        picked = dd.apply_selection(self.records, contains="gallery")
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["aweme_id"], "7300000000000000003")

    # --- capture dedupe ---

    def test_capture_append_dedupes(self) -> None:
        session = dd.create_session(self.tmp, "测试创作者", "post")
        sid = session["session_id"]
        added_first = dd.append_captured(self.tmp, sid, self.records)
        added_second = dd.append_captured(self.tmp, sid, self.records)
        self.assertEqual(added_first, 3)
        self.assertEqual(added_second, 0)
        self.assertEqual(len(dd.load_captured(self.tmp, sid)), 3)

    # --- download pipeline (HTTP faked) ---

    def test_download_writes_files_index_and_scrubbed_meta(self) -> None:
        calls = []

        def fake_get_bytes(url, cookie="", timeout=60, max_bytes=0):
            calls.append(url)
            return b"FAKE-BINARY-DATA"

        original = dd.http_get_bytes
        dd.http_get_bytes = fake_get_bytes
        try:
            out = self.tmp / "out"
            result = dd.download_records(self.records, out, quality="best", throttle=dd.DownloadThrottle(0, 0))
        finally:
            dd.http_get_bytes = original

        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["success_count"], 3)
        self.assertEqual(result["failure_count"], 0)

        # video for record 1 uses the HD url
        self.assertIn("https://v.example.com/play1_hd.mp4", calls)
        # index.csv has a header + 3 rows
        index = out / "index.csv"
        self.assertTrue(index.exists())
        lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 4)

        # video file present for record 1
        videos = list((out / "videos").glob("001-*.mp4"))
        self.assertEqual(len(videos), 1)
        # image files present for record 3
        self.assertTrue((out / "images" / "003" / "01.jpg").exists())
        self.assertTrue((out / "images" / "003" / "02.jpg").exists())

        # metadata scrubbed: record 2's a_bogus signature must not be persisted
        meta2 = (out / "meta" / "002.json").read_text(encoding="utf-8")
        self.assertNotIn("a_bogus", meta2)
        self.assertNotIn("SECRETSIG", meta2)

    def test_download_falls_back_across_candidates(self) -> None:
        def flaky_get_bytes(url, cookie="", timeout=60, max_bytes=0):
            if url == "https://v.example.com/play1_hd.mp4":
                raise ValueError("hd mirror down")
            return b"DATA"

        original = dd.http_get_bytes
        dd.http_get_bytes = flaky_get_bytes
        try:
            out = self.tmp / "out2"
            result = dd.download_records([self.records[0]], out, throttle=dd.DownloadThrottle(0, 0))
        finally:
            dd.http_get_bytes = original
        # first candidate failed, but a later candidate succeeds -> success
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(len(list((out / "videos").glob("*.mp4"))), 1)

    def test_resolve_aweme_id_from_url_and_id(self) -> None:
        self.assertEqual(dd.resolve_aweme_id("7300000000000000009"), "7300000000000000009")
        self.assertEqual(
            dd.resolve_aweme_id("https://www.douyin.com/video/7300000000000000009?x=1"),
            "7300000000000000009",
        )
        self.assertEqual(dd.resolve_aweme_id("https://www.douyin.com/user/foo"), "")


if __name__ == "__main__":
    unittest.main()
