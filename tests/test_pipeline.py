"""End-to-end smoke test for the mock pipeline."""

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


if __name__ == "__main__":
    unittest.main()
