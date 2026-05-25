"""End-to-end smoke test for the mock pipeline + targeted unit tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest import mock as _mock

from aicrew import db
from aicrew.pipeline import PipelineRunner
from aicrew.seed import seed
from aicrew.settings import load_settings


class PipelineSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_test_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "test.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()

    def test_seed_then_full_run(self) -> None:
        info = seed(self.settings)
        self.assertEqual(info["status"], "created")
        runner = PipelineRunner(self.settings)
        summary = runner.run_full(info["project_id"])
        self.assertGreater(summary.topics_generated, 0)
        self.assertGreater(summary.articles_written, 0)
        self.assertGreater(summary.posts_created, 0)
        with db.connect(self.settings.db_path) as conn:
            agents = conn.execute(
                "SELECT role, language FROM agents WHERE project_id=? ORDER BY role, language",
                (info["project_id"],),
            ).fetchall()
            roles = {(a["role"], a["language"]) for a in agents}
        # both RU and EN article writers exist
        self.assertIn(("article_writer", "ru"), roles)
        self.assertIn(("article_writer", "en"), roles)
        # at least one channel rewriter per text channel
        rewriters = [r for r in roles if r[0] == "channel_rewriter"]
        self.assertGreaterEqual(len(rewriters), 1)


class TemplatesTest(unittest.TestCase):
    def test_render_basic(self) -> None:
        from aicrew.templates import render

        out = render(
            "Hello {{ name }}! "
            "{% if greet %}Welcome.{% else %}Bye.{% endif %}",
            {"name": "world", "greet": True},
        )
        self.assertIn("Hello world", out)
        self.assertIn("Welcome", out)

    def test_render_for_loop(self) -> None:
        from aicrew.templates import render

        out = render(
            "{% for x in items %}- {{ x }}\n{% endfor %}",
            {"items": ["a", "b", "c"]},
        )
        self.assertEqual(out.strip(), "- a\n- b\n- c")


class CryptoTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        from aicrew.crypto import decrypt, encrypt

        key = "x" * 32
        token = encrypt("hello-world", key)
        self.assertNotIn("hello", token)
        self.assertEqual(decrypt(token, key), "hello-world")


class ChannelsTest(unittest.TestCase):
    def test_all_kinds_have_guides(self) -> None:
        from aicrew.channels.registry import CHANNEL_KINDS

        self.assertEqual(len(CHANNEL_KINDS), 14)
        for kind, spec in CHANNEL_KINDS.items():
            self.assertTrue(spec.connect_guide_md, f"{kind} missing connect guide")
            self.assertTrue(spec.label, kind)


class PublishersTest(unittest.TestCase):
    def test_mock_adapter_for_each_kind(self) -> None:
        from aicrew.channels.registry import CHANNEL_KINDS
        from aicrew.publishers import get_publisher

        s = load_settings()
        for kind, spec in CHANNEL_KINDS.items():
            adapter = get_publisher(kind)
            creds = {k: "x" for k, _ in spec.credentials_fields}
            res = adapter.publish(post={"id": "po_test"}, creds=creds, settings=s)
            self.assertTrue(res.ok, f"{kind}: publish failed")
            self.assertTrue(res.external_url.startswith("https://mock."))


class TavilySearchTest(unittest.TestCase):
    """Tests for aicrew/tools/search.py — Tavily adapter, cache, budget."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_search_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "search.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        # Make sure schema is created so search_cache table exists.
        db.init_schema(os.environ["AICREW_DB"])

    def tearDown(self) -> None:
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("TAVILY_DAILY_BUDGET", None)

    def test_mock_provider_returns_deterministic_results(self) -> None:
        from aicrew.tools.search import web_search

        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        s = load_settings()
        out = web_search("space race 1969", settings=s, max_results=3)
        self.assertEqual(len(out), 3)
        self.assertIn("space race 1969", out[0]["title"])
        self.assertEqual(out[0]["url"].startswith("https://mock.example/"), True)

    def test_tavily_no_key_returns_empty_no_crash(self) -> None:
        from aicrew.tools.search import web_search

        os.environ["AICREW_SEARCH_PROVIDER"] = "tavily"
        os.environ.pop("TAVILY_API_KEY", None)
        s = load_settings()
        out = web_search("any query", settings=s)
        self.assertEqual(out, [])

    def test_tavily_call_then_cache_hit(self) -> None:
        from aicrew.tools import search as _search

        os.environ["AICREW_SEARCH_PROVIDER"] = "tavily"
        os.environ["TAVILY_API_KEY"] = "tvly-test"
        s = load_settings()
        fake_results = [
            {"title": "T1", "url": "https://a", "content": "c1", "score": 0.9, "rank": 1},
            {"title": "T2", "url": "https://b", "content": "c2", "score": 0.7, "rank": 2},
        ]
        with _mock.patch.object(_search, "_tavily_call", return_value=fake_results) as m:
            r1 = _search.web_search("today historical events", settings=s)
            r2 = _search.web_search("today historical events", settings=s)
        # First call hit Tavily, second call hit cache.
        self.assertEqual(m.call_count, 1)
        self.assertEqual(r1, r2)
        self.assertEqual(len(r1), 2)
        # Stored in search_cache.
        with db.connect(s.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) c FROM search_cache").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_tavily_daily_budget_blocks_calls(self) -> None:
        from aicrew.tools import search as _search

        os.environ["AICREW_SEARCH_PROVIDER"] = "tavily"
        os.environ["TAVILY_API_KEY"] = "tvly-test"
        os.environ["TAVILY_DAILY_BUDGET"] = "1"
        s = load_settings()
        fake_results = [{"title": "T1", "url": "https://a", "content": "c1",
                         "score": 0.9, "rank": 1}]
        with _mock.patch.object(_search, "_tavily_call", return_value=fake_results) as m:
            r1 = _search.web_search("query A", settings=s)        # consumes 1 budget
            r2 = _search.web_search("query B", settings=s)        # blocked by budget
            r3 = _search.web_search("query A", settings=s)        # cache hit, NOT counted
        self.assertEqual(m.call_count, 1)
        self.assertEqual(len(r1), 1)
        self.assertEqual(r2, [])  # budget exhausted
        self.assertEqual(r3, r1)  # cache returns same as r1


class ForbiddenTopicsTest(unittest.TestCase):
    """When the user re-runs the pipeline within memory_lookback_days, topics
    that were already produced (regardless of publish status) must NOT appear
    in the new generator pass. This guards against duplicates and wasted spend.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_forbidden_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "forbidden.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()

    def test_lookback_includes_unpublished_topics(self) -> None:
        info = seed(self.settings)
        runner = PipelineRunner(self.settings)
        # Run only the topic phase — articles never written, posts never published.
        runner.run_topic_phase(info["project_id"])
        first_titles = set()
        with db.connect(self.settings.db_path) as conn:
            for r in conn.execute(
                "SELECT title FROM topics WHERE project_id=?",
                (info["project_id"],),
            ).fetchall():
                first_titles.add(r["title"])
        self.assertGreater(len(first_titles), 0)
        # Ask the runner what it considers "forbidden" right now: it MUST
        # include all titles from the first run, even though none of them
        # have been published.
        forbidden = runner._memory_forbidden_titles(
            info["project_id"], lookback_days=30,
        )
        self.assertEqual(set(forbidden), first_titles)


# ----------------------------------------------------------------------------
# Wan 2.7 async path tests. We mock urllib.request.urlopen to verify the wire
# format (URL + body), the polling loop, and the final image download.
# ----------------------------------------------------------------------------


def _http_response(body: bytes):
    """Build a fake context manager returned by urlopen(...) -> response."""
    class _Resp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload
        def read(self) -> bytes:
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    return _Resp(body)


class WanAsyncFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_wan_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "wan.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "302ai"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        os.environ["AI302_API_KEY"] = "sk-302-test"
        self.settings = load_settings()

    def test_wan27_submit_poll_download(self) -> None:
        """Verifies the full happy-path:
         1) submit goes to /aliyun/api/v1/services/aigc/image-generation/generation
            with input.messages[].content[].text and parameters.enable_interleave=true
         2) poll hits /aliyun/api/v1/tasks/<task_id> and reads task_status
         3) the final URL from output.choices[0].message.content[0].image is
            downloaded and saved.
        """
        from aicrew.tools import image_gen

        # 1x1 PNG (smallest valid).
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000d49444154789c63600100000005000150fd9b520000000049454e44"
            "ae426082"
        )
        submit_resp = json.dumps({
            "request_id": "rq-1",
            "output": {"task_id": "task-abc", "task_status": "PENDING"},
        }).encode("utf-8")
        # First poll is RUNNING, second is SUCCEEDED with an image URL.
        running_resp = json.dumps({
            "request_id": "rq-2",
            "output": {"task_id": "task-abc", "task_status": "RUNNING"},
        }).encode("utf-8")
        succeeded_resp = json.dumps({
            "request_id": "rq-3",
            "output": {
                "task_id": "task-abc",
                "task_status": "SUCCEEDED",
                "choices": [{
                    "message": {
                        "content": [
                            {"image": "https://files.302.ai/wan/result-1.png"},
                        ],
                    },
                }],
            },
        }).encode("utf-8")

        seen_urls: list[str] = []
        seen_methods: list[str] = []
        seen_bodies: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            # urlopen() accepts either a Request object or a bare URL string
            # (for the final image download). Handle both.
            if isinstance(req, str):
                url, method, data = req, "GET", b""
            else:
                url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
                method = req.get_method()
                data = req.data or b""
            seen_urls.append(url)
            seen_methods.append(method)
            seen_bodies.append(data)
            if "image-generation/generation" in url and method == "POST":
                return _http_response(submit_resp)
            if "/tasks/task-abc" in url and method == "GET":
                # alternate between RUNNING (first call) then SUCCEEDED.
                idx = sum(1 for u in seen_urls if "/tasks/task-abc" in u)
                if idx == 1:
                    return _http_response(running_resp)
                return _http_response(succeeded_resp)
            if url == "https://files.302.ai/wan/result-1.png":
                return _http_response(png)
            raise AssertionError(f"unexpected url: {url} method={method}")

        # Skip the polling sleep for the test.
        with _mock.patch.object(image_gen, "POLL_INTERVAL_SEC", 0), \
             _mock.patch.object(image_gen, "POLL_TIMEOUT_SEC", 30), \
             _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = image_gen.generate_image(
                "an astronaut riding a horse",
                settings=self.settings, idx=0,
                model="302ai:wan2.7-image",
            )

        # Submit URL is correct.
        submit_url = seen_urls[0]
        self.assertEqual(seen_methods[0], "POST")
        self.assertTrue(
            submit_url.endswith(
                "/aliyun/api/v1/services/aigc/image-generation/generation"
            ),
            f"unexpected submit url: {submit_url}",
        )
        # Submit body uses messages format with text + enable_interleave=true.
        submit_body = json.loads(seen_bodies[0].decode("utf-8"))
        self.assertEqual(submit_body["model"], "wan2.7-image")
        msgs = submit_body["input"]["messages"]
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"][0]["text"],
                         "an astronaut riding a horse")
        self.assertTrue(submit_body["parameters"]["enable_interleave"])
        self.assertEqual(submit_body["parameters"]["n"], 1)
        # We polled at least twice (RUNNING then SUCCEEDED).
        poll_count = sum(1 for u in seen_urls if "/tasks/task-abc" in u)
        self.assertGreaterEqual(poll_count, 2)
        # And we downloaded the image URL.
        self.assertIn("https://files.302.ai/wan/result-1.png", seen_urls)
        # The result file exists on disk.
        self.assertTrue(res.storage_url.startswith("/media/"))
        local_path = os.path.join(self.settings.media_dir,
                                   res.storage_url.rsplit("/", 1)[-1])
        self.assertTrue(os.path.exists(local_path))
        self.assertGreater(os.path.getsize(local_path), 0)

    def test_wan_failed_task_raises_and_writes_error_sidecar(self) -> None:
        """If 302.ai reports task FAILED, we should fall back to placeholder
        AND write an .error.txt sidecar with the failure message."""
        from aicrew.tools import image_gen

        submit_resp = json.dumps({
            "request_id": "rq-1",
            "output": {"task_id": "task-fail", "task_status": "PENDING"},
        }).encode("utf-8")
        failed_resp = json.dumps({
            "request_id": "rq-2",
            "output": {
                "task_id": "task-fail",
                "task_status": "FAILED",
                "code": "InvalidParameter",
                "message": "size out of range",
            },
        }).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "image-generation/generation" in url:
                return _http_response(submit_resp)
            if "/tasks/task-fail" in url:
                return _http_response(failed_resp)
            raise AssertionError(f"unexpected url: {url}")

        with _mock.patch.object(image_gen, "POLL_INTERVAL_SEC", 0), \
             _mock.patch.object(image_gen, "POLL_TIMEOUT_SEC", 30), \
             _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = image_gen.generate_image(
                "test", settings=self.settings, idx=0,
                model="302ai:wan2.7-image",
            )
        # Got a placeholder back, not a real image.
        self.assertTrue(res.storage_url.startswith("/media/"))
        # Sidecar .error.txt exists with the failure reason.
        files = os.listdir(self.settings.media_dir)
        err_files = [f for f in files if f.endswith(".error.txt")]
        self.assertEqual(len(err_files), 1, f"expected one error sidecar; got {files}")
        with open(os.path.join(self.settings.media_dir, err_files[0]),
                  "r", encoding="utf-8") as fh:
            err_content = fh.read()
        self.assertIn("size out of range", err_content)
        self.assertIn("302ai:wan2.7-image", err_content)


class SchedulerTimeTest(unittest.TestCase):
    """slot 'HH:MM' + project timezone → next future UTC datetime."""

    def test_next_slot_utc_today_in_future(self) -> None:
        from datetime import datetime, timezone
        from aicrew.scheduler import next_slot_utc
        # Now = 2026-05-25 06:00 UTC = 09:00 Europe/Moscow
        # Slot 12:00 МСК → 12:00 МСК сегодня = 09:00 UTC сегодня
        now = datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc)
        out = next_slot_utc("12:00", "Europe/Moscow", now_utc=now)
        self.assertEqual(out.year, 2026)
        self.assertEqual(out.month, 5)
        self.assertEqual(out.day, 25)
        self.assertEqual(out.hour, 9)  # 12 МСК = 09 UTC летом
        self.assertEqual(out.minute, 0)

    def test_next_slot_utc_already_passed_rolls_to_tomorrow(self) -> None:
        from datetime import datetime, timezone
        from aicrew.scheduler import next_slot_utc
        # Now = 2026-05-25 15:00 UTC = 18:00 Europe/Moscow
        # Slot 12:00 МСК уже прошёл сегодня → должен быть завтра в 12:00 МСК
        now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)
        out = next_slot_utc("12:00", "Europe/Moscow", now_utc=now)
        self.assertEqual(out.day, 26)
        self.assertEqual(out.hour, 9)  # 12 МСК = 09 UTC

    def test_next_slot_utc_unknown_tz_falls_back(self) -> None:
        from datetime import datetime, timezone
        from aicrew.scheduler import next_slot_utc
        now = datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc)
        out = next_slot_utc("12:00", "Bogus/Nowhere", now_utc=now)
        # Must not crash; should fall back to UTC interpretation.
        self.assertEqual(out.hour, 12)


if __name__ == "__main__":
    unittest.main()
