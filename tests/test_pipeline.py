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


if __name__ == "__main__":
    unittest.main()
