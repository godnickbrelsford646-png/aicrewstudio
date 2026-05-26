"""End-to-end smoke test for the mock pipeline + targeted unit tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
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


class VideoPhaseTest(unittest.TestCase):
    """End-to-end mock test for PipelineRunner.run_video_phase.

    Seeds the demo project, picks one already-written article, runs the
    video phase, and asserts the expected media_assets rows + the final
    MP4 file on disk.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_video_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "video.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        # Make sure we don't accidentally hit any real API: a present
        # AI302_API_KEY/OPENAI_API_KEY would route the helpers to the real
        # path. The tools fall back to mock when the key is empty.
        for k in ("AI302_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        self.settings = load_settings()

    def test_run_video_phase_mock(self) -> None:
        info = seed(self.settings)
        # Need at least one article; the cheapest path is to run topic + article
        # phases of the existing pipeline (mock-mode is fast and writes nothing
        # to the network).
        runner = PipelineRunner(self.settings)
        runner.run_topic_phase(info["project_id"])
        article_ids = runner.run_article_phase(info["project_id"], max_articles=1)
        self.assertTrue(article_ids, "article phase produced no articles")
        # Pick a Russian article (project default language).
        with db.connect(self.settings.db_path) as conn:
            row = conn.execute(
                "SELECT id, language FROM articles WHERE id IN ({}) "
                "ORDER BY language LIMIT 1".format(",".join(["?"] * len(article_ids))),
                article_ids,
            ).fetchone()
        self.assertIsNotNone(row, "no article row to test against")
        article_id = row["id"]
        article_lang = row["language"]
        # Run the video phase.
        result = runner.run_video_phase(article_id)
        # Basic shape checks.
        self.assertTrue(result["final_video_url"].startswith("/media/"),
                         f"unexpected url: {result['final_video_url']}")
        self.assertGreaterEqual(result["scenes_count"], 5)
        self.assertLessEqual(result["scenes_count"], 9)
        self.assertGreater(result["duration_s"], 0)
        # media_assets sanity.
        with db.connect(self.settings.db_path) as conn:
            chosen_video = conn.execute(
                "SELECT * FROM media_assets WHERE article_id=? "
                "AND kind='video' AND chosen=1",
                (article_id,),
            ).fetchall()
            audios = conn.execute(
                "SELECT * FROM media_assets WHERE article_id=? AND kind='audio'",
                (article_id,),
            ).fetchall()
            subtitles = conn.execute(
                "SELECT * FROM media_assets WHERE article_id=? AND kind='subtitle'",
                (article_id,),
            ).fetchall()
        self.assertEqual(len(chosen_video), 1, "expected exactly one chosen=1 video row")
        self.assertEqual(len(audios), result["scenes_count"],
                          "expected one audio row per scene")
        self.assertGreaterEqual(len(subtitles), 1, "expected at least one subtitle row")
        # Final MP4 file present on disk with the canonical name.
        final_path = os.path.join(
            self.settings.media_dir, f"{article_id}_{article_lang}.mp4"
        )
        self.assertTrue(os.path.exists(final_path), f"missing {final_path}")
        self.assertGreater(os.path.getsize(final_path), 0)


class EnabledTeamsTest(unittest.TestCase):
    """The enabled_teams toggle must gate the article and video phases.

    With only text_ru enabled:
      - run_article_phase produces RU articles only (text_en is off);
      - run_video_phase on any article fails (video_ru and video_en
        are both off).
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_teams_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "teams.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        for k in ("AI302_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        self.settings = load_settings()

    def test_disabled_text_team_skips_language(self) -> None:
        info = seed(self.settings)
        # Disable everything except text_ru. Seed creates a project with
        # all four teams enabled; we patch the column directly to simulate
        # the user clicking three of them off in the Settings tab.
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE projects SET enabled_teams=? WHERE id=?",
                (json.dumps(["text_ru"]), info["project_id"]),
            )
        runner = PipelineRunner(self.settings)
        runner.run_topic_phase(info["project_id"])
        article_ids = runner.run_article_phase(info["project_id"])
        # Articles must all be Russian — text_en is disabled.
        with db.connect(self.settings.db_path) as conn:
            langs = [r["language"] for r in conn.execute(
                "SELECT language FROM articles WHERE project_id=?",
                (info["project_id"],),
            ).fetchall()]
        self.assertTrue(article_ids, "expected at least one article")
        self.assertTrue(
            all(l == "ru" for l in langs),
            f"expected only ru articles; got {langs}",
        )
        # Video phase must refuse to run on a RU article because
        # video_ru is disabled in the patched enabled_teams list.
        ru_article = article_ids[0]
        with self.assertRaises(RuntimeError) as cm:
            runner.run_video_phase(ru_article)
        msg = str(cm.exception).lower()
        self.assertIn("video team", msg)
        self.assertIn("disabled", msg)


class WanI2VAsyncFlowTest(unittest.TestCase):
    """Tests the real 302.ai Wan 2.2-i2v image-to-video async flow.

    Mocks urllib.request.urlopen and verifies:
      1. submit goes to /aliyun/api/v1/services/aigc/video-generation/video-synthesis
         with input.{prompt, img_url} and parameters.{duration, size};
      2. img_url is the absolute URL composed via $AICREW_PUBLIC_BASE_URL
         (302.ai needs to fetch the keyframe over HTTPS, our /media/ paths
         are relative);
      3. polling /aliyun/api/v1/tasks/<task_id> alternates RUNNING then
         SUCCEEDED, and we read task_status correctly;
      4. the video URL from output.results[0].video_url is downloaded and
         saved as an MP4 under settings.media_dir.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_i2v_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "i2v.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        os.environ["AI302_API_KEY"] = "sk-302-test"
        os.environ["AICREW_PUBLIC_BASE_URL"] = "https://mepoststream.site"
        self.settings = load_settings()

    def tearDown(self) -> None:
        for k in ("AI302_API_KEY", "AICREW_PUBLIC_BASE_URL"):
            os.environ.pop(k, None)

    def test_wan22_i2v_submit_poll_download(self) -> None:
        from aicrew.tools import video_gen

        # Minimal-but-valid MP4 bytes for the "downloaded" clip.
        mp4_bytes = (
            b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
            + b"\x00" * 80
        )
        submit_resp = json.dumps({
            "request_id": "rq-1",
            "output": {"task_id": "task-i2v", "task_status": "PENDING"},
        }).encode("utf-8")
        running_resp = json.dumps({
            "request_id": "rq-2",
            "output": {"task_id": "task-i2v", "task_status": "RUNNING"},
        }).encode("utf-8")
        succeeded_resp = json.dumps({
            "request_id": "rq-3",
            "output": {
                "task_id": "task-i2v",
                "task_status": "SUCCEEDED",
                "results": [
                    {"video_url": "https://files.302.ai/wan-i2v/result-1.mp4"},
                ],
            },
        }).encode("utf-8")

        seen: list[tuple[str, str, bytes]] = []

        def fake_urlopen(req, timeout=None):
            if isinstance(req, str):
                url, method, data = req, "GET", b""
            else:
                url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
                method = req.get_method()
                data = req.data or b""
            seen.append((url, method, data))
            if "video-generation/video-synthesis" in url and method == "POST":
                return _http_response(submit_resp)
            if "/tasks/task-i2v" in url and method == "GET":
                idx = sum(1 for u, _, _ in seen if "/tasks/task-i2v" in u)
                if idx == 1:
                    return _http_response(running_resp)
                return _http_response(succeeded_resp)
            if url == "https://files.302.ai/wan-i2v/result-1.mp4":
                return _http_response(mp4_bytes)
            raise AssertionError(f"unexpected url: {url} method={method}")

        with _mock.patch.object(video_gen, "POLL_INTERVAL_SEC", 0), \
             _mock.patch.object(video_gen, "POLL_TIMEOUT_SEC", 30), \
             _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = video_gen.generate_video_clip(
                "/media/keyframe_test.png",
                "slow zoom in on a battlefield",
                settings=self.settings, idx=1,
                model="302ai:wan2.2-i2v",
                duration_s=5.0,
            )

        # Submit URL is correct.
        submit_url, submit_method, submit_body_raw = seen[0]
        self.assertEqual(submit_method, "POST")
        self.assertTrue(
            submit_url.endswith(
                "/aliyun/api/v1/services/aigc/video-generation/video-synthesis"
            ),
            f"unexpected submit url: {submit_url}",
        )
        # Submit body uses input.img_url (NOT image_url, NOT input_image),
        # passes the prompt through, and includes integer duration + 9:16 size.
        submit_body = json.loads(submit_body_raw.decode("utf-8"))
        self.assertEqual(submit_body["model"], "wan2.2-i2v")
        self.assertEqual(
            submit_body["input"]["prompt"],
            "slow zoom in on a battlefield",
        )
        # img_url must be the absolute URL composed via AICREW_PUBLIC_BASE_URL.
        self.assertEqual(
            submit_body["input"]["img_url"],
            "https://mepoststream.site/media/keyframe_test.png",
        )
        self.assertEqual(submit_body["parameters"]["duration"], 5)
        self.assertEqual(submit_body["parameters"]["size"], "1080*1920")
        # Polled at least twice (RUNNING -> SUCCEEDED).
        poll_count = sum(1 for u, _, _ in seen if "/tasks/task-i2v" in u)
        self.assertGreaterEqual(poll_count, 2)
        # Downloaded the result URL.
        urls = [u for u, _, _ in seen]
        self.assertIn("https://files.302.ai/wan-i2v/result-1.mp4", urls)
        # MP4 file exists on disk.
        self.assertTrue(res.storage_url.startswith("/media/"))
        local_path = os.path.join(
            self.settings.media_dir, res.storage_url.rsplit("/", 1)[-1],
        )
        self.assertTrue(os.path.exists(local_path))
        self.assertGreater(os.path.getsize(local_path), 0)

    def test_wan_i2v_failed_task_writes_error_sidecar(self) -> None:
        """If 302.ai reports task FAILED, we should fall back to placeholder
        AND write a vclip_<hash>.error.txt sidecar with the failure message."""
        from aicrew.tools import video_gen

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
                "message": "img_url not reachable",
            },
        }).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "video-generation/video-synthesis" in url:
                return _http_response(submit_resp)
            if "/tasks/task-fail" in url:
                return _http_response(failed_resp)
            raise AssertionError(f"unexpected url: {url}")

        with _mock.patch.object(video_gen, "POLL_INTERVAL_SEC", 0), \
             _mock.patch.object(video_gen, "POLL_TIMEOUT_SEC", 30), \
             _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = video_gen.generate_video_clip(
                "/media/anything.png",
                "any motion",
                settings=self.settings, idx=2,
                model="302ai:wan2.2-i2v",
                duration_s=5.0,
            )
        # We get a placeholder back, not a real video.
        self.assertTrue(res.storage_url.startswith("/media/"))
        # Sidecar .error.txt exists with the failure reason.
        files = os.listdir(self.settings.media_dir)
        err_files = [f for f in files
                     if f.startswith("vclip_") and f.endswith(".error.txt")]
        self.assertEqual(
            len(err_files), 1,
            f"expected one error sidecar; got {files}",
        )
        with open(os.path.join(self.settings.media_dir, err_files[0]),
                  "r", encoding="utf-8") as fh:
            err_content = fh.read()
        self.assertIn("img_url not reachable", err_content)
        self.assertIn("302ai:wan2.2-i2v", err_content)


class OpenAITTSFlowTest(unittest.TestCase):
    """Tests the real OpenAI gpt-4o-mini-tts /v1/audio/speech flow.

    Mocks urllib.request.urlopen and verifies:
      1. POST goes to https://api.openai.com/v1/audio/speech;
      2. body has the correct model/voice/speed/input fields and
         response_format='mp3';
      3. the response bytes are saved verbatim as the local .mp3.
    Also covers the 302.ai proxy variant ('302ai:gpt-4o-mini-tts'):
      * the request goes to https://api.302.ai/v1/audio/speech;
      * AI302_API_KEY is used (not OPENAI_API_KEY);
      * the bare model name ('gpt-4o-mini-tts') ends up in the body.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_tts_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "tts.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()

    def tearDown(self) -> None:
        for k in ("OPENAI_API_KEY", "AI302_API_KEY"):
            os.environ.pop(k, None)

    def _mp3_bytes(self) -> bytes:
        # Tiny but valid-looking MP3 body that the provider would return.
        return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" + b"\x00" * 64

    def test_openai_tts_request_and_save(self) -> None:
        from aicrew.tools import tts_gen
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"
        mp3 = self._mp3_bytes()
        seen: list[tuple[str, str, bytes, dict]] = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            method = req.get_method()
            data = req.data or b""
            headers = dict(req.headers) if hasattr(req, "headers") else {}
            seen.append((url, method, data, headers))
            return _http_response(mp3)

        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = tts_gen.generate_tts(
                "Здравствуйте, это тест озвучки.",
                settings=self.settings, idx=1,
                model="openai:gpt-4o-mini-tts",
                voice_id="onyx", speed=1.1, language="ru",
            )

        self.assertEqual(len(seen), 1, "expected exactly one HTTP call")
        url, method, body_raw, headers = seen[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api.openai.com/v1/audio/speech")
        # Authorization header carries the OpenAI key.
        # urllib lower-cases header keys when storing them.
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        self.assertEqual(auth, "Bearer sk-openai-test")
        body = json.loads(body_raw.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-4o-mini-tts")
        self.assertEqual(body["voice"], "onyx")
        self.assertEqual(body["speed"], 1.1)
        self.assertEqual(body["input"], "Здравствуйте, это тест озвучки.")
        self.assertEqual(body["response_format"], "mp3")
        # MP3 bytes saved on disk under media_dir.
        local_path = os.path.join(
            self.settings.media_dir, res.storage_url.rsplit("/", 1)[-1],
        )
        self.assertTrue(os.path.exists(local_path))
        with open(local_path, "rb") as fh:
            saved = fh.read()
        self.assertEqual(saved, mp3)
        self.assertEqual(res.mime, "audio/mpeg")

    def test_302ai_tts_uses_proxy_endpoint_and_key(self) -> None:
        from aicrew.tools import tts_gen
        os.environ["AI302_API_KEY"] = "sk-302-test"
        mp3 = self._mp3_bytes()
        seen: list[tuple[str, str, bytes, dict]] = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            method = req.get_method()
            data = req.data or b""
            headers = dict(req.headers) if hasattr(req, "headers") else {}
            seen.append((url, method, data, headers))
            return _http_response(mp3)

        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tts_gen.generate_tts(
                "Hello world.",
                settings=self.settings, idx=2,
                model="302ai:gpt-4o-mini-tts",
                voice_id="echo", speed=1.0, language="en",
            )
        url, method, body_raw, headers = seen[0]
        self.assertEqual(url, "https://api.302.ai/v1/audio/speech")
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        self.assertEqual(auth, "Bearer sk-302-test")
        body = json.loads(body_raw.decode("utf-8"))
        # Bare model name (without the 302ai: prefix) goes in the body.
        self.assertEqual(body["model"], "gpt-4o-mini-tts")
        self.assertEqual(body["voice"], "echo")

    def test_tts_http_error_writes_sidecar_and_uses_placeholder(self) -> None:
        from aicrew.tools import tts_gen
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized",
                {}, io.BytesIO(b'{"error":{"message":"bad key"}}'),
            )

        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = tts_gen.generate_tts(
                "anything",
                settings=self.settings, idx=3,
                model="openai:gpt-4o-mini-tts",
                voice_id="onyx",
            )
        # We get a placeholder, not garbage from the failed call.
        self.assertTrue(res.storage_url.startswith("/media/"))
        files = os.listdir(self.settings.media_dir)
        err_files = [f for f in files
                     if f.startswith("tts_") and f.endswith(".error.txt")]
        self.assertEqual(
            len(err_files), 1,
            f"expected one tts error sidecar; got {files}",
        )
        with open(os.path.join(self.settings.media_dir, err_files[0]),
                  "r", encoding="utf-8") as fh:
            err_content = fh.read()
        self.assertIn("HTTP 401", err_content)
        self.assertIn("openai:gpt-4o-mini-tts", err_content)
        self.assertIn("onyx", err_content)


class WhisperTranscribeFlowTest(unittest.TestCase):
    """Tests the real OpenAI whisper-1 /v1/audio/transcriptions flow.

    Mocks urllib.request.urlopen and verifies:
      1. POST goes to the right endpoint;
      2. Content-Type is multipart/form-data with a real boundary;
      3. the body contains form fields model='whisper-1',
         response_format='srt', language=<lang>, and a file part with
         the verbatim audio bytes;
      4. the response body (SRT text) is returned through TranscribeResult;
      5. Authorization header carries the right key per provider.
    Also covers the 302.ai proxy variant and the HTTP 4xx fallback path
    that writes a sidecar and returns mock SRT.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_whisper_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "whisper.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()
        os.makedirs(self.settings.media_dir, exist_ok=True)
        # A small but non-trivial fake MP3 the helper will upload.
        self.audio_bytes = (
            b"ID3\x03\x00\x00\x00\x00\x00\x00"
            + b"\xff\xfb" + b"\x12\x34" * 200  # ~400 distinguishable bytes
        )
        self.audio_name = "tts_dummy.mp3"
        with open(os.path.join(self.settings.media_dir, self.audio_name), "wb") as fh:
            fh.write(self.audio_bytes)

    def tearDown(self) -> None:
        for k in ("OPENAI_API_KEY", "AI302_API_KEY"):
            os.environ.pop(k, None)

    @staticmethod
    def _good_srt_response() -> bytes:
        return (
            b"1\n00:00:00,000 --> 00:00:03,500\n"
            b"Hello from Whisper.\n\n"
            b"2\n00:00:03,500 --> 00:00:07,000\n"
            b"Second line.\n"
        )

    def _capture_request(self, holder: list, response_body: bytes):
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            method = req.get_method()
            data = req.data or b""
            headers = dict(req.headers) if hasattr(req, "headers") else {}
            holder.append((url, method, data, headers))
            return _http_response(response_body)
        return fake_urlopen

    def test_openai_whisper_request_and_parse(self) -> None:
        from aicrew.tools import whisper_transcribe
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"
        seen: list = []
        srt_resp = self._good_srt_response()
        with _mock.patch(
            "urllib.request.urlopen",
            side_effect=self._capture_request(seen, srt_resp),
        ):
            res = whisper_transcribe.transcribe_audio(
                f"/media/{self.audio_name}",
                settings=self.settings, language="ru",
            )
        # Exactly one HTTP call.
        self.assertEqual(len(seen), 1)
        url, method, body, headers = seen[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api.openai.com/v1/audio/transcriptions")
        # Authorization picked the OpenAI key.
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        self.assertEqual(auth, "Bearer sk-openai-test")
        # Multipart Content-Type with a real boundary (urllib normalises
        # header names to title-case, so check both spellings).
        ct = headers.get("Content-type") or headers.get("Content-Type") or ""
        self.assertTrue(ct.startswith("multipart/form-data; boundary="),
                         f"unexpected Content-Type: {ct!r}")
        # Body contains form fields. We don't fully parse multipart —
        # presence checks are sufficient and decoupled from boundary specifics.
        self.assertIn(b'name="model"', body)
        self.assertIn(b'whisper-1', body)
        self.assertIn(b'name="response_format"', body)
        self.assertIn(b'srt', body)
        self.assertIn(b'name="language"', body)
        # ISO 639-1 only (first two chars, lowercase).
        self.assertIn(b'\r\n\r\nru\r\n', body)
        # File part with the original filename and the verbatim audio bytes.
        self.assertIn(b'name="file"', body)
        self.assertIn(b'filename="tts_dummy.mp3"', body)
        self.assertIn(b'Content-Type: audio/mpeg', body)
        self.assertIn(self.audio_bytes, body)
        # Returned SRT is exactly what the provider sent.
        self.assertEqual(res.srt_text, srt_resp.decode("utf-8"))
        # Duration estimated from the last "-->" timestamp.
        self.assertAlmostEqual(res.duration_s, 7.0, places=2)

    def test_302ai_whisper_uses_proxy_endpoint_and_key(self) -> None:
        from aicrew.tools import whisper_transcribe
        os.environ["AI302_API_KEY"] = "sk-302-test"
        seen: list = []
        srt_resp = self._good_srt_response()
        with _mock.patch(
            "urllib.request.urlopen",
            side_effect=self._capture_request(seen, srt_resp),
        ):
            whisper_transcribe.transcribe_audio(
                f"/media/{self.audio_name}",
                settings=self.settings, language="en",
                model="302ai:whisper-1",
            )
        url, _, body, headers = seen[0]
        self.assertEqual(url, "https://api.302.ai/v1/audio/transcriptions")
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        self.assertEqual(auth, "Bearer sk-302-test")
        # Bare model name in the body.
        self.assertIn(b"whisper-1", body)
        # Language switched to en.
        self.assertIn(b'\r\n\r\nen\r\n', body)

    def test_whisper_http_error_writes_sidecar_and_returns_mock_srt(self) -> None:
        from aicrew.tools import whisper_transcribe
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 413, "Payload Too Large",
                {}, io.BytesIO(b'{"error":{"message":"file too large"}}'),
            )

        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = whisper_transcribe.transcribe_audio(
                f"/media/{self.audio_name}",
                settings=self.settings, language="ru",
            )
        # Returned SRT is the deterministic mock fallback (4 segments, RU line).
        self.assertIn("Сегмент 1 автотранскрипции.", res.srt_text)
        self.assertGreaterEqual(res.duration_s, 1.0)
        # Sidecar exists and mentions the failure.
        files = os.listdir(self.settings.media_dir)
        err_files = [f for f in files if f.endswith(".error.txt")]
        self.assertEqual(
            len(err_files), 1,
            f"expected one whisper error sidecar; got {files}",
        )
        with open(os.path.join(self.settings.media_dir, err_files[0]),
                  "r", encoding="utf-8") as fh:
            err_content = fh.read()
        self.assertIn("HTTP 413", err_content)
        self.assertIn("openai:whisper-1", err_content)
        self.assertIn(self.audio_name, err_content)

    def test_whisper_missing_audio_file_returns_mock_without_call(self) -> None:
        """If the upstream TTS step did not produce a file, we should NOT
        attempt the API call and just return mock SRT cleanly."""
        from aicrew.tools import whisper_transcribe
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"
        seen_calls: list = []

        def fake_urlopen(req, timeout=None):
            seen_calls.append(req)
            return _http_response(self._good_srt_response())

        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = whisper_transcribe.transcribe_audio(
                "/media/file_that_does_not_exist.mp3",
                settings=self.settings, language="en",
            )
        # No HTTP call attempted.
        self.assertEqual(seen_calls, [])
        # Mock SRT (English line because language='en').
        self.assertIn("Auto-transcribed segment 1.", res.srt_text)


class FfmpegAssemblyTest(unittest.TestCase):
    """Tests the real ffmpeg orchestration in
    aicrew/tools/video_assembler.py via mocked shutil.which + subprocess.run.

    The sandbox has no ffmpeg, so we fake its presence and capture the
    command line / write a real-looking output file so the helper accepts
    the result.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_ffmpeg_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "ffmpeg.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()
        os.makedirs(self.settings.media_dir, exist_ok=True)
        # Two fake "scene" files. The bytes are nonsense — ffmpeg would
        # reject them, but our subprocess.run is mocked.
        self.clip_paths = []
        self.audio_paths = []
        for i in range(3):
            cp = os.path.join(self.settings.media_dir, f"vclip_{i}.mp4")
            ap = os.path.join(self.settings.media_dir, f"tts_{i}.mp3")
            with open(cp, "wb") as fh:
                fh.write(b"\x00" * 200)
            with open(ap, "wb") as fh:
                fh.write(b"\x00" * 200)
            self.clip_paths.append(cp)
            self.audio_paths.append(ap)
        self.ass_path = os.path.join(self.settings.media_dir, "subs.ass")
        with open(self.ass_path, "w", encoding="utf-8") as fh:
            fh.write("[Script Info]\nScriptType: v4.00+\n")
        self.scenes = [
            {"clip_path": self.clip_paths[i],
             "audio_path": self.audio_paths[i],
             "ass_path": self.ass_path,
             "duration_s": 5.0}
            for i in range(3)
        ]

    def _fake_run_writes_output(self, output_size: int = 4096):
        """Build a subprocess.run mock that creates the output file the
        helper checks afterwards (>1KB else the helper throws)."""
        from types import SimpleNamespace
        captured: dict = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = list(cmd)
            captured["timeout"] = kwargs.get("timeout")
            # ffmpeg is invoked with the output path as the LAST arg.
            out = cmd[-1]
            with open(out, "wb") as fh:
                fh.write(b"\x00" * output_size)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return fake_run, captured

    def test_assemble_invokes_ffmpeg_with_correct_filter_graph(self) -> None:
        from aicrew.tools import video_assembler
        fake_run, captured = self._fake_run_writes_output()
        with _mock.patch.object(video_assembler.shutil, "which",
                                  return_value="/usr/bin/ffmpeg"), \
             _mock.patch.object(video_assembler.subprocess, "run",
                                 side_effect=fake_run):
            res = video_assembler.assemble_video(
                self.scenes,
                settings=self.settings,
                article_id="ar_test",
                language="ru",
            )
        cmd = captured["cmd"]
        # Sanity: it's an ffmpeg invocation with -y and the right output.
        self.assertEqual(cmd[0], "/usr/bin/ffmpeg")
        self.assertIn("-y", cmd)
        self.assertEqual(cmd[-1],
                          os.path.join(self.settings.media_dir, "ar_test_ru.mp4"))
        # All 6 input files (3 clips + 3 audio) are passed via -i.
        i_args = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
        for p in self.clip_paths + self.audio_paths:
            self.assertIn(p, i_args)
        # filter_complex is present and contains the per-scene chains
        # (scale to 1080:1920, trim, concat=n=3, ass=...).
        self.assertIn("-filter_complex", cmd)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("scale=1080:1920", fc)
        self.assertIn("crop=1080:1920", fc)
        self.assertIn("trim=duration=5.000", fc)
        self.assertIn("concat=n=3:v=1:a=1", fc)
        # Subtitles burn-in references our ASS file (with ":" escaped).
        self.assertIn("ass=", fc)
        self.assertIn(self.ass_path.replace(":", r"\:"), fc)
        # Encoder settings: H.264 main + AAC + faststart.
        self.assertIn("libx264", cmd)
        self.assertIn("aac", cmd)
        self.assertIn("+faststart", cmd)
        # AssembleResult has the expected shape.
        self.assertEqual(res.storage_url, "/media/ar_test_ru.mp4")
        self.assertEqual(res.width, 1080)
        self.assertEqual(res.height, 1920)
        self.assertAlmostEqual(res.duration_s, 15.0, places=2)

    def test_assemble_falls_back_to_placeholder_on_ffmpeg_error(self) -> None:
        from aicrew.tools import video_assembler
        from types import SimpleNamespace

        def fake_run(cmd, *a, **kw):
            return SimpleNamespace(returncode=1, stdout="",
                                    stderr="moov atom not found")

        with _mock.patch.object(video_assembler.shutil, "which",
                                  return_value="/usr/bin/ffmpeg"), \
             _mock.patch.object(video_assembler.subprocess, "run",
                                 side_effect=fake_run):
            res = video_assembler.assemble_video(
                self.scenes,
                settings=self.settings,
                article_id="ar_fail",
                language="en",
            )
        # Output exists (placeholder fallback).
        local_path = os.path.join(self.settings.media_dir, "ar_fail_en.mp4")
        self.assertTrue(os.path.exists(local_path))
        # ftyp magic identifies it as the placeholder.
        with open(local_path, "rb") as fh:
            head = fh.read(32)
        self.assertIn(b"ftypisom", head)
        # Sidecar .error.txt was written with the failure reason.
        err_path = local_path + ".error.txt"
        self.assertTrue(os.path.exists(err_path))
        with open(err_path, "r", encoding="utf-8") as fh:
            err_content = fh.read()
        self.assertIn("ar_fail", err_content)
        self.assertIn("moov atom not found", err_content)
        # AssembleResult still ok-shaped.
        self.assertEqual(res.storage_url, "/media/ar_fail_en.mp4")

    def test_assemble_uses_placeholder_when_ffmpeg_missing(self) -> None:
        from aicrew.tools import video_assembler

        with _mock.patch.object(video_assembler.shutil, "which",
                                  return_value=None), \
             _mock.patch.object(video_assembler.subprocess, "run") as run_mock:
            res = video_assembler.assemble_video(
                self.scenes,
                settings=self.settings,
                article_id="ar_nomf",
                language="ru",
            )
        # subprocess.run NOT called when ffmpeg missing.
        run_mock.assert_not_called()
        # Output is the placeholder (same ftyp magic).
        local_path = os.path.join(self.settings.media_dir, "ar_nomf_ru.mp4")
        self.assertTrue(os.path.exists(local_path))
        with open(local_path, "rb") as fh:
            self.assertIn(b"ftypisom", fh.read(32))
        self.assertEqual(res.storage_url, "/media/ar_nomf_ru.mp4")

    def test_concat_audio_files_uses_ffmpeg_concat_demuxer(self) -> None:
        from aicrew.tools import video_assembler
        fake_run, captured = self._fake_run_writes_output()
        with _mock.patch.object(video_assembler.shutil, "which",
                                  return_value="/usr/bin/ffmpeg"), \
             _mock.patch.object(video_assembler.subprocess, "run",
                                 side_effect=fake_run):
            out = video_assembler.concat_audio_files(
                self.audio_paths,
                settings=self.settings,
                out_basename="combined.mp3",
            )
        # Output path is under media_dir.
        self.assertEqual(out, os.path.join(self.settings.media_dir, "combined.mp3"))
        # ffmpeg called with -f concat -safe 0 -c copy.
        cmd = captured["cmd"]
        self.assertIn("-f", cmd)
        self.assertEqual(cmd[cmd.index("-f") + 1], "concat")
        self.assertIn("-safe", cmd)
        self.assertEqual(cmd[cmd.index("-safe") + 1], "0")
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

    def test_concat_audio_files_returns_first_when_only_one(self) -> None:
        from aicrew.tools import video_assembler
        with _mock.patch.object(video_assembler.subprocess, "run") as run_mock:
            out = video_assembler.concat_audio_files(
                [self.audio_paths[0]],
                settings=self.settings,
                out_basename="combined.mp3",
            )
        # No ffmpeg call — single input shortcut.
        run_mock.assert_not_called()
        self.assertEqual(out, self.audio_paths[0])

    def test_concat_audio_files_returns_first_when_ffmpeg_missing(self) -> None:
        from aicrew.tools import video_assembler
        with _mock.patch.object(video_assembler.shutil, "which",
                                  return_value=None), \
             _mock.patch.object(video_assembler.subprocess, "run") as run_mock:
            out = video_assembler.concat_audio_files(
                self.audio_paths,
                settings=self.settings,
                out_basename="combined.mp3",
            )
        run_mock.assert_not_called()
        # First path returned as a passable substitute.
        self.assertEqual(out, self.audio_paths[0])


class ProjectCostsEndpointTest(unittest.TestCase):
    """Smoke test for GET /api/projects/<slug>/costs.

    We don't spin up an HTTP server — we call the route handler directly
    with a thin AicrewHandler stand-in, the same pattern used implicitly
    by the rest of the test suite via PipelineRunner. The agent_runs
    rows are inserted by the topic phase under PipelineSmokeTest's
    seed; here we just call the cost helper to verify the JSON shape.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_costs_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "costs.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()

    def test_costs_endpoint_returns_expected_shape(self) -> None:
        # Seed and run topic+article phases so agent_runs has rows.
        info = seed(self.settings)
        runner = PipelineRunner(self.settings)
        runner.run_topic_phase(info["project_id"])
        runner.run_article_phase(info["project_id"], max_articles=1)

        # Build a stand-in handler that captures the JSON payload the
        # route would send.
        from aicrew import api as api_mod
        captured: dict = {}

        class _StubHandler:
            settings = self.settings
            def send_json(self, status, payload):
                captured["status"] = status
                captured["payload"] = payload

        # Resolve project slug.
        with db.connect(self.settings.db_path) as conn:
            proj = conn.execute(
                "SELECT slug FROM projects WHERE id=?",
                (info["project_id"],),
            ).fetchone()
        # Find the costs handler in the route table by pattern match.
        handler = None
        for method, pattern, fn in api_mod._ROUTES:
            if method == "GET" and pattern == "/api/projects/{pkey}/costs":
                handler = fn
                break
        self.assertIsNotNone(handler, "costs route is not registered")
        handler(_StubHandler(), {"pkey": proj["slug"]})

        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        # Expected top-level keys.
        for key in ("today_usd", "month_usd", "budget_usd_month",
                    "by_role", "by_phase", "by_day"):
            self.assertIn(key, payload, f"missing key {key}")
        # Types and ranges.
        self.assertIsInstance(payload["today_usd"], float)
        self.assertIsInstance(payload["month_usd"], float)
        self.assertIsInstance(payload["budget_usd_month"], float)
        self.assertGreaterEqual(payload["today_usd"], 0)
        self.assertGreaterEqual(payload["month_usd"], 0)
        # 14-day timeseries, oldest-first, all entries shaped {date, total_usd}.
        self.assertEqual(len(payload["by_day"]), 14)
        for entry in payload["by_day"]:
            self.assertIn("date", entry)
            self.assertIn("total_usd", entry)
            self.assertIsInstance(entry["total_usd"], float)
        # by_role / by_phase entries (may be empty for a fresh project,
        # but agent_runs definitely have rows after topic+article phases).
        self.assertIsInstance(payload["by_role"], list)
        self.assertIsInstance(payload["by_phase"], list)
        # We just ran the topic phase, so we should see at least one
        # phase entry. Mock cost is zero, so we only check structure
        # (runs > 0).
        self.assertGreater(len(payload["by_phase"]), 0,
                            "expected at least one phase row after seeding "
                            "and running a phase")
        for r in payload["by_phase"]:
            self.assertIn("phase", r)
            self.assertIn("runs", r)
            self.assertIn("total_usd", r)
            self.assertGreaterEqual(r["runs"], 1)
        # Same for by_role: topic_generator at minimum.
        roles_seen = {r["role"] for r in payload["by_role"]}
        self.assertIn("topic_generator", roles_seen)


class AntiHallucinationTest(unittest.TestCase):
    """Tests for the anti-hallucination hardening pass.

    Covers:
      - registration of the new fact_audit role,
      - idempotent migration that syncs ZADNIM prompts and
        creates missing fact_audit agents,
      - lowered article_writer temperature,
      - research_validator now has a non-empty Tavily query template,
      - fact_audit actually fires for every text language during a
        full mock pipeline run.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_anti_halluc_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "anti.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()

    # --- 1) role registration ------------------------------------------------

    def test_fact_audit_role_registered(self) -> None:
        from aicrew.agents.registry import (
            ROLES,
            LANG_SCOPED_ROLES,
            TEAM_KIND_FOR_ROLE,
            role_spec,
        )
        self.assertIn("fact_audit", ROLES)
        self.assertIn("fact_audit", LANG_SCOPED_ROLES)
        self.assertEqual(TEAM_KIND_FOR_ROLE.get("fact_audit"), "text")
        spec = role_spec("fact_audit")
        self.assertEqual(spec.role, "fact_audit")
        # Has a non-trivial prompt template (the registry stub at minimum).
        self.assertGreater(len(spec.prompt_template), 50)
        # Default min_score = 80 (stricter than qa_editorial's 75).
        self.assertEqual(spec.default_params.get("min_score"), 80)

    def test_fact_audit_param_schema_exposed(self) -> None:
        from aicrew.agents.param_schema import PARAM_SCHEMAS, schema_for
        self.assertIn("fact_audit", PARAM_SCHEMAS)
        fields = schema_for("fact_audit")
        self.assertEqual([f.key for f in fields], ["min_score"])
        # The audit bar is stricter: default 80 (qa_editorial defaults to 75).
        self.assertEqual(fields[0].default, 80)

    # --- 2) integration: fact_audit runs in the pipeline --------------------

    def test_fact_audit_in_pipeline(self) -> None:
        info = seed(self.settings)
        runner = PipelineRunner(self.settings)
        runner.run_full(info["project_id"])
        with db.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                "SELECT a.role, a.language FROM agent_runs ar "
                "JOIN agents a ON a.id = ar.agent_id "
                "WHERE a.project_id=? AND a.role='fact_audit'",
                (info["project_id"],),
            ).fetchall()
        seen_langs = {r["language"] for r in rows}
        self.assertGreater(len(rows), 0,
                           "fact_audit must produce at least one agent_run")
        # Demo project has both ru and en text teams.
        self.assertIn("ru", seen_langs)
        self.assertIn("en", seen_langs)

    # --- 3) migration: creates missing fact_audit ---------------------------

    def test_zadnim_migration_creates_fact_audit(self) -> None:
        # Seed once to get a project with the full agent set.
        info = seed(self.settings)
        project_id = info["project_id"]
        # Simulate an "old" DB by deleting the fact_audit agents that
        # seed() just created; init_schema must put them back.
        with db.connect(self.settings.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM agents WHERE project_id=? AND role='fact_audit'",
                (project_id,),
            )
            self.assertGreater(cur.rowcount, 0,
                               "preconditions: seed should produce fact_audit agents")
            remaining = conn.execute(
                "SELECT COUNT(*) AS c FROM agents WHERE project_id=? AND role='fact_audit'",
                (project_id,),
            ).fetchone()["c"]
            self.assertEqual(remaining, 0)
        # Re-run init_schema (the migration that should re-seed fact_audit).
        db.init_schema(self.settings.db_path)
        with db.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                "SELECT language FROM agents WHERE project_id=? AND role='fact_audit' "
                "ORDER BY language",
                (project_id,),
            ).fetchall()
        langs = sorted(r["language"] for r in rows)
        self.assertEqual(langs, ["en", "ru"],
                         "migration must seed one fact_audit agent per language")

    # --- 4) migration: refreshes prompts ------------------------------------

    def test_zadnim_migration_updates_prompts(self) -> None:
        info = seed(self.settings)
        project_id = info["project_id"]
        # Pick the article_writer (ru) and stomp its prompt to simulate
        # an older deployment running with stale text.
        with db.connect(self.settings.db_path) as conn:
            agent = conn.execute(
                "SELECT id, prompt_template FROM agents "
                "WHERE project_id=? AND role='article_writer' AND language='ru'",
                (project_id,),
            ).fetchone()
            self.assertIsNotNone(agent, "article_writer (ru) should exist")
            original_len = len(agent["prompt_template"])
            self.assertGreater(original_len, 100)
            conn.execute(
                "UPDATE agents SET prompt_template='' WHERE id=?",
                (agent["id"],),
            )
            zeroed = conn.execute(
                "SELECT prompt_template FROM agents WHERE id=?",
                (agent["id"],),
            ).fetchone()
            self.assertEqual(zeroed["prompt_template"], "")
        # Re-run init_schema; the sync step should restore the prompt.
        db.init_schema(self.settings.db_path)
        with db.connect(self.settings.db_path) as conn:
            restored = conn.execute(
                "SELECT prompt_template FROM agents "
                "WHERE project_id=? AND role='article_writer' AND language='ru'",
                (project_id,),
            ).fetchone()
        self.assertGreater(len(restored["prompt_template"]), 100,
                           "init_schema must restore article_writer prompt")
        # The restored prompt must include one of the new ground-rule
        # markers, proving it's the new version, not just non-empty.
        self.assertIn("GROUND RULES", restored["prompt_template"])

    # --- 5) migration: idempotent ------------------------------------------

    def test_zadnim_migration_idempotent(self) -> None:
        info = seed(self.settings)
        project_id = info["project_id"]

        def count_agents() -> int:
            with db.connect(self.settings.db_path) as conn:
                return conn.execute(
                    "SELECT COUNT(*) AS c FROM agents WHERE project_id=?",
                    (project_id,),
                ).fetchone()["c"]

        first = count_agents()
        db.init_schema(self.settings.db_path)
        second = count_agents()
        db.init_schema(self.settings.db_path)
        third = count_agents()
        self.assertEqual(first, second,
                         "first re-run of init_schema must not change agent count")
        self.assertEqual(second, third,
                         "second re-run of init_schema must not change agent count")

    # --- 6) topic_validator: per-candidate query template -------------------

    def test_topic_validator_per_candidate_search(self) -> None:
        from aicrew.seed import ZADNIM_AGENT_PARAMS
        tpl = ZADNIM_AGENT_PARAMS["topic_validator"]["search_query_template"]
        # The template MUST address a specific candidate, not the whole
        # day. Anything else and the validator goes back to checking
        # "{{ today_md }} historical events" in bulk (the bug we are
        # fixing).
        self.assertIn("candidate_topics[0].title", tpl)
        self.assertIn("candidate_topics[0].event_date", tpl)
        # And the generic per-day template must be gone.
        self.assertNotIn("historical events fact check", tpl)

    # --- 7) research_validator: search re-enabled ---------------------------

    def test_research_validator_search_enabled(self) -> None:
        from aicrew.seed import ZADNIM_AGENT_PARAMS
        params = ZADNIM_AGENT_PARAMS["research_validator"]
        self.assertNotEqual(params.get("search_query_template", ""), "",
                            "research_validator must run a second-pass search")
        self.assertIn("topic.title", params["search_query_template"])
        self.assertEqual(params.get("search_depth"), "advanced")
        self.assertGreaterEqual(int(params.get("search_max_results", 0)), 5)

    # --- 8) article_writer: temperature lowered ----------------------------

    def test_article_writer_temperature_lowered(self) -> None:
        from aicrew.seed import ZADNIM_AGENT_CONFIG
        model, temperature, max_tokens = ZADNIM_AGENT_CONFIG["article_writer"]
        # Bug: temperature 0.8 invited fabricated "unexpected details".
        # Fix: 0.4 keeps prose alive without inviting confabulation.
        self.assertLessEqual(temperature, 0.5,
                             f"article_writer temperature must be <= 0.5, got {temperature}")
        # Defensive: not so cold the prose dies.
        self.assertGreaterEqual(temperature, 0.2)


if __name__ == "__main__":
    unittest.main()



# ============================================================================
# Media-cost tracking
#
# Cost-tracking dashboard previously summed only agent_runs.cost_usd. Image
# / video / TTS / Whisper calls write their own assets to media_assets but
# their per-call charge was lost. The fix routes a cost_usd value through
# every media tool, persists it on the media_assets row, and aggregates it
# into the /api/projects/{slug}/costs endpoint.
#
# Tests cover four layers:
#   1) pure pricing helpers in aicrew/llm/pricing.py (no I/O);
#   2) the soft-migration that adds media_assets.cost_usd, idempotent;
#   3) tool return shape — generate_image must surface cost_usd;
#   4) the /costs endpoint — synthetic media_assets rows must show up
#      in today_usd / month_usd / by_role / by_phase / by_day.
# ============================================================================


class PricingHelpersTest(unittest.TestCase):
    """Pure-function tests for aicrew/llm/pricing.py — no DB, no HTTP."""

    def test_estimate_image_cost(self) -> None:
        from aicrew.llm.pricing import estimate_image_cost
        # Wan 2.7: $0.05 per image at the published 302.ai rate.
        self.assertAlmostEqual(estimate_image_cost("302ai:wan2.7-image", 1), 0.05, places=6)
        # Three images bill linearly.
        self.assertAlmostEqual(estimate_image_cost("302ai:wan2.7-image", 3), 0.15, places=6)
        # Mock and unknown models price at $0 (lossy-zero by design).
        self.assertEqual(estimate_image_cost("mock:placeholder", 1), 0.0)
        self.assertEqual(estimate_image_cost("nonexistent:model", 1), 0.0)
        # Negative count clamps to 0 — a paranoid guard against bad inputs.
        self.assertEqual(estimate_image_cost("302ai:wan2.7-image", -5), 0.0)

    def test_estimate_video_cost(self) -> None:
        from aicrew.llm.pricing import estimate_video_cost
        # Wan 2.2-i2v: $0.12 per 5-second clip.
        self.assertAlmostEqual(estimate_video_cost("302ai:wan2.2-i2v", 5.0), 0.12, places=6)
        # 10-second clip = 2x the 5s rate.
        self.assertAlmostEqual(estimate_video_cost("302ai:wan2.2-i2v", 10.0), 0.24, places=6)
        # Mock = $0.
        self.assertEqual(estimate_video_cost("mock:placeholder", 5.0), 0.0)
        # Negative duration clamps to 0.
        self.assertEqual(estimate_video_cost("302ai:wan2.2-i2v", -3.0), 0.0)

    def test_estimate_tts_cost(self) -> None:
        from aicrew.llm.pricing import estimate_tts_cost
        # gpt-4o-mini-tts: $0.6 per 1M chars => 1000 chars = $0.0006.
        self.assertAlmostEqual(estimate_tts_cost("openai:gpt-4o-mini-tts", 1000),
                               0.0006, places=6)
        # 1M chars hits the headline rate exactly.
        self.assertAlmostEqual(estimate_tts_cost("openai:gpt-4o-mini-tts", 1_000_000),
                               0.6, places=6)
        # Mock = $0.
        self.assertEqual(estimate_tts_cost("mock:placeholder", 1000), 0.0)

    def test_estimate_whisper_cost(self) -> None:
        from aicrew.llm.pricing import estimate_whisper_cost
        # Whisper-1: $0.006 per minute => 60s = $0.006.
        self.assertAlmostEqual(estimate_whisper_cost("openai:whisper-1", 60.0),
                               0.006, places=6)
        # Two minutes = 2x.
        self.assertAlmostEqual(estimate_whisper_cost("openai:whisper-1", 120.0),
                               0.012, places=6)
        # 302.ai proxy: same rate.
        self.assertAlmostEqual(estimate_whisper_cost("302ai:whisper-1", 60.0),
                               0.006, places=6)
        # Mock = $0.
        self.assertEqual(estimate_whisper_cost("mock:placeholder", 60.0), 0.0)


class MediaAssetsSchemaTest(unittest.TestCase):
    """The soft-migration that adds media_assets.cost_usd."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_costs_")
        self.db_path = os.path.join(self.tmpdir, "schema.db")

    def test_media_assets_cost_column_exists(self) -> None:
        db.init_schema(self.db_path)
        with db.connect(self.db_path) as conn:
            cols = [r["name"]
                    for r in conn.execute(
                        "PRAGMA table_info(media_assets)").fetchall()]
        self.assertIn("cost_usd", cols,
                      "media_assets.cost_usd must be added by init_schema()")

    def test_media_assets_migration_idempotent(self) -> None:
        # Running init_schema twice on the same DB must not raise.
        db.init_schema(self.db_path)
        db.init_schema(self.db_path)
        # And the column is still present (we didn't accidentally drop it).
        with db.connect(self.db_path) as conn:
            cols = [r["name"]
                    for r in conn.execute(
                        "PRAGMA table_info(media_assets)").fetchall()]
        self.assertIn("cost_usd", cols)

    def test_media_assets_migration_old_db(self) -> None:
        """Backfill on a DB created without cost_usd: ALTER adds the column."""
        # Step 1: create a media_assets table without cost_usd, mimicking a
        # production DB that pre-dates the cost-tracking work.
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE media_assets ("
                "id TEXT PRIMARY KEY, "
                "article_id TEXT, project_id TEXT NOT NULL, kind TEXT NOT NULL, "
                "language TEXT, prompt TEXT NOT NULL DEFAULT '', "
                "model TEXT NOT NULL DEFAULT '', storage_url TEXT NOT NULL, "
                "mime TEXT NOT NULL DEFAULT '', width INTEGER, height INTEGER, "
                "duration_s REAL, chosen INTEGER NOT NULL DEFAULT 0, "
                "meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO media_assets (id, project_id, kind, storage_url, "
                "created_at) VALUES (?,?,?,?,?)",
                ("ma_old1", "p_old", "image", "/media/old.png",
                 "2024-01-01T00:00:00Z"),
            )
            conn.commit()
        # Step 2: init_schema must add the column without dropping data.
        db.init_schema(self.db_path)
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, cost_usd FROM media_assets WHERE id=?", ("ma_old1",)
            ).fetchone()
        self.assertIsNotNone(row)
        # Backfilled rows default to 0 — that's the lossy-zero contract.
        self.assertEqual(float(row["cost_usd"]), 0.0)


class MediaToolsCostShapeTest(unittest.TestCase):
    """Tool result objects must surface cost_usd; mock paths must be free."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_tools_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "tools.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        # Make sure no real-API keys are present so the tools take the
        # mock path even when the test host has them set.
        for k in ("OPENAI_API_KEY", "AI302_API_KEY"):
            os.environ.pop(k, None)
        self.settings = load_settings()

    def test_image_gen_returns_cost_mock(self) -> None:
        from aicrew.tools.image_gen import generate_image
        res = generate_image("test prompt", settings=self.settings, idx=0,
                             model="mock:placeholder")
        self.assertTrue(hasattr(res, "cost_usd"),
                        "ImageResult must expose cost_usd")
        self.assertEqual(res.cost_usd, 0.0,
                         "Mock image must cost $0")

    def test_tts_gen_returns_cost_mock(self) -> None:
        from aicrew.tools.tts_gen import generate_tts
        res = generate_tts("hello world", settings=self.settings, idx=0,
                           model="mock:placeholder", voice_id="onyx")
        self.assertTrue(hasattr(res, "cost_usd"))
        self.assertEqual(res.cost_usd, 0.0)

    def test_video_gen_returns_cost_mock(self) -> None:
        from aicrew.tools.video_gen import generate_video_clip
        res = generate_video_clip("/media/x.png", "slow zoom",
                                  settings=self.settings, idx=0,
                                  model="mock:placeholder", duration_s=5.0)
        self.assertTrue(hasattr(res, "cost_usd"))
        self.assertEqual(res.cost_usd, 0.0)

    def test_whisper_returns_cost_mock(self) -> None:
        from aicrew.tools.whisper_transcribe import transcribe_audio
        res = transcribe_audio("/media/missing.mp3", settings=self.settings,
                               language="en", model="mock:placeholder")
        self.assertTrue(hasattr(res, "cost_usd"))
        self.assertEqual(res.cost_usd, 0.0)


class CostsEndpointMediaTest(unittest.TestCase):
    """End-to-end check: media_assets.cost_usd must surface in /costs.

    We exercise the endpoint by directly invoking ``_project_costs`` with a
    fake handler — far simpler than spinning up the ThreadingHTTPServer in
    tests, and it covers exactly the SQL aggregation we care about.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="aicrew_costsep_")
        os.environ["AICREW_DB"] = os.path.join(self.tmpdir, "costs.db")
        os.environ["AICREW_LLM_PROVIDER"] = "mock"
        os.environ["AICREW_IMAGE_PROVIDER"] = "mock"
        os.environ["AICREW_SEARCH_PROVIDER"] = "mock"
        os.environ["AICREW_MEDIA_DIR"] = os.path.join(self.tmpdir, "media")
        self.settings = load_settings()
        # Make sure the schema exists; the test does not need a full seed.
        db.init_schema(self.settings.db_path)
        # Insert a minimal user + project so _resolve_project finds us.
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO users (id, email, created_at) VALUES (?,?,?)",
                ("u_test", "t@example.com", db.now_iso()),
            )
            conn.execute(
                "INSERT INTO projects (id, user_id, slug, name, niche, "
                "description, is_enabled, timezone, language_modes, "
                "daily_topics_target, daily_articles_target, "
                "budget_usd_month, style_guide, enabled_teams, "
                "created_at, updated_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("p_test", "u_test", "test-project", "Test Project",
                 "tests", "", 1, "UTC", '["ru"]', 1, 1, 50.0, "",
                 '["text_ru","text_en","video_ru","video_en"]',
                 db.now_iso(), db.now_iso()),
            )

    def _call_costs(self) -> dict:
        """Invoke ``api._project_costs`` with a fake handler that captures
        the JSON body instead of writing to a socket.
        """
        from aicrew import api as api_mod

        captured: dict = {}

        class _FakeHandler:
            def __init__(self, settings):
                self.settings = settings

            def send_json(self, status, payload):
                captured["status"] = status
                captured["body"] = payload

        h = _FakeHandler(self.settings)
        api_mod._project_costs(h, {"pkey": "test-project"})
        self.assertEqual(captured.get("status"), 200,
                         f"costs endpoint returned non-200: {captured}")
        return captured["body"]

    def test_costs_endpoint_includes_image_media(self) -> None:
        # Fake an image asset created today, $0.05 (Wan 2.7 single image).
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO media_assets (id, project_id, kind, storage_url, "
                "cost_usd, created_at) VALUES (?,?,?,?,?,?)",
                ("ma_img1", "p_test", "image", "/media/x.png", 0.05,
                 db.now_iso()),
            )
        body = self._call_costs()
        # Image cost must show up in today_usd AND month_usd.
        self.assertGreaterEqual(body["today_usd"], 0.05 - 1e-9,
                                f"today_usd missing image cost: {body}")
        self.assertGreaterEqual(body["month_usd"], 0.05 - 1e-9,
                                f"month_usd missing image cost: {body}")
        # by_role[] must contain a synthetic __media_image__ row.
        roles = {r["role"]: r for r in body["by_role"]}
        self.assertIn("__media_image__", roles,
                      f"by_role missing __media_image__: {body['by_role']}")
        self.assertAlmostEqual(roles["__media_image__"]["total_usd"], 0.05,
                               places=6)
        # by_phase[] must contain 'media' (image -> media phase, not video).
        phases = {r["phase"]: r for r in body["by_phase"]}
        self.assertIn("media", phases,
                      f"by_phase missing 'media' synthetic row: {body['by_phase']}")
        self.assertAlmostEqual(phases["media"]["total_usd"], 0.05, places=6)

    def test_costs_endpoint_video_phase(self) -> None:
        # Wan i2v scene clip: $0.12 -> goes to 'video' phase, NOT 'media'.
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO media_assets (id, project_id, kind, storage_url, "
                "cost_usd, created_at) VALUES (?,?,?,?,?,?)",
                ("ma_vid1", "p_test", "video", "/media/clip.mp4", 0.12,
                 db.now_iso()),
            )
        body = self._call_costs()
        phases = {r["phase"]: r for r in body["by_phase"]}
        self.assertIn("video", phases,
                      f"by_phase missing 'video' synthetic row: {body['by_phase']}")
        self.assertAlmostEqual(phases["video"]["total_usd"], 0.12, places=6)
        # And NOT in 'media' (we route 'video' kind to its own phase).
        self.assertNotIn("media", phases,
                         "video kind must not leak into 'media' phase")
        # by_role[]: __media_video__ row exists.
        roles = {r["role"]: r for r in body["by_role"]}
        self.assertIn("__media_video__", roles)

    def test_costs_endpoint_by_day_includes_media(self) -> None:
        # Insert an asset with an explicit timestamp = today.
        # Format must be SQLite datetime() compatible (YYYY-MM-DD HH:MM:SS
        # works; ISO with 'T' and 'Z' is also accepted by date()).
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO media_assets (id, project_id, kind, storage_url, "
                "cost_usd, created_at) VALUES (?,?,?,?,?,?)",
                ("ma_audio1", "p_test", "audio", "/media/v.mp3", 0.001,
                 db.now_iso()),
            )
        body = self._call_costs()
        # 14-day window, oldest first; today's bucket is the LAST one.
        self.assertEqual(len(body["by_day"]), 14)
        self.assertGreaterEqual(body["by_day"][-1]["total_usd"], 0.001 - 1e-9,
                                f"today's by_day bucket missing media cost: "
                                f"{body['by_day'][-1]}")


if __name__ == "__main__":
    unittest.main()
