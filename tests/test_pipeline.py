"""End-to-end smoke test for the mock pipeline + targeted unit tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

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
        from unittest import mock as _mock
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
        from unittest import mock as _mock
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


if __name__ == "__main__":
    unittest.main()
