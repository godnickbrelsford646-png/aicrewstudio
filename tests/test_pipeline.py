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



class ResponsesApiParseTest(unittest.TestCase):
    """Tests for the Responses API output parser used by the OpenAI
    web_search path. We do NOT make network calls — we just feed the
    parser realistic payloads and check that text + URL citations are
    extracted."""

    def test_extract_message_text_and_annotations(self) -> None:
        from aicrew.llm.adapter import _extract_responses_text

        payload = {
            "output": [
                {"type": "web_search_call", "id": "ws_1"},
                {
                    "type": "message",
                    "id": "msg_1",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"topics": [{"title": "X"}]}',
                            "annotations": [
                                {"type": "url_citation",
                                 "url": "https://example.com/a",
                                 "title": "Source A"},
                            ],
                        }
                    ],
                },
            ]
        }
        text, ann = _extract_responses_text(payload)
        self.assertIn('"topics"', text)
        self.assertEqual(len(ann), 1)
        self.assertEqual(ann[0]["url"], "https://example.com/a")

    def test_falls_back_to_output_text_field(self) -> None:
        from aicrew.llm.adapter import _extract_responses_text

        payload = {"output": [], "output_text": '{"ok": true}'}
        text, ann = _extract_responses_text(payload)
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(ann, [])

    def test_handles_text_block_variant(self) -> None:
        from aicrew.llm.adapter import _extract_responses_text

        payload = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "text", "text": "{}", "annotations": []},
                    ],
                }
            ]
        }
        text, ann = _extract_responses_text(payload)
        self.assertEqual(text, "{}")
        self.assertEqual(ann, [])


class WebSearchRoutingTest(unittest.TestCase):
    """The adapter must NOT try to use /v1/responses on the 302.ai gateway
    (it doesn't expose web_search). It should warn and fall back to
    /v1/chat/completions silently. We can verify that without network
    by mocking the chat path."""

    def test_falls_back_when_routed_to_302ai(self) -> None:
        from unittest import mock
        from aicrew.llm.adapter import OpenAILLMAdapter, LLMResponse

        adapter = OpenAILLMAdapter(openai_key="sk-test", ai302_key="sk-302")
        # Patch the responses path to fail loudly if reached.
        with mock.patch.object(
            adapter, "_call_responses_with_web_search",
            side_effect=AssertionError("must not be called for 302.ai"),
        ):
            # Patch urlopen so chat/completions doesn't actually fire.
            fake_body = (
                b'{"choices":[{"message":{"content":"{\\"ok\\":1}"}}],'
                b'"usage":{"prompt_tokens":3,"completion_tokens":4}}'
            )
            class _Resp:
                def read(self_inner): return fake_body
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
            with mock.patch("urllib.request.urlopen", return_value=_Resp()):
                resp = adapter.call(
                    model="302ai:gpt-4o",
                    prompt="hi",
                    temperature=0.5,
                    max_tokens=100,
                    response_schema=None,
                    agent_role="topic_generator",
                    agent_params={},
                    inputs={},
                    language="ru",
                    use_web_search=True,
                )
        self.assertIsInstance(resp, LLMResponse)
        self.assertEqual(resp.parsed, {"ok": 1})
        self.assertEqual(resp.raw["provider"], "openai-compat")
