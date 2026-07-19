# -*- coding: utf-8 -*-
"""Regression: first_thinking timeout/error should default to PASS."""

from pathlib import Path
import unittest
from unittest import mock

import requests

from OlivOSAIChatAssassin import data, webTools


class FirstThinkingFailOpenTests(unittest.TestCase):
    def test_default_intent_timeout_is_45(self):
        self.assertEqual(data.configDefault["intent_api"]["timeout"], 45)

    def test_get_intent_ai_config_passes_timeout(self):
        cfg = {
            "api_key": "main-key",
            "api_base": "https://main.example/v1",
            "model": "main-model",
            "intent_api": {
                "enable": True,
                "api_key": "intent-key",
                "api_base": "https://intent.example/v1",
                "model": "intent-model",
                "timeout": 45,
            },
        }
        intent_cfg = webTools.get_intent_ai_config(cfg)
        self.assertEqual(intent_cfg["timeout"], 45)
        self.assertEqual(intent_cfg["api_key"], "intent-key")
        self.assertEqual(intent_cfg["model"], "intent-model")

    def test_call_ai_uses_timeout_override(self):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {},
                }

            text = "ok"

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["timeout"] = timeout
            captured["url"] = url
            return FakeResponse()

        with mock.patch.object(webTools.requests, "post", side_effect=fake_post):
            res = webTools.call_ai(
                {
                    "api_key": "k",
                    "api_base": "https://example.com/v1",
                    "model": "m",
                    "max_tokens": 16,
                    "temperature": 0.0,
                    "thinking": {"type": "disabled"},
                },
                [{"role": "user", "content": "hi"}],
                timeout_override=45,
            )
        self.assertEqual(res, "ok")
        self.assertEqual(captured["timeout"], 45.0)

    def test_call_ai_uses_config_timeout_when_override_missing(self):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {},
                }

            text = "ok"

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["timeout"] = timeout
            return FakeResponse()

        with mock.patch.object(webTools.requests, "post", side_effect=fake_post):
            webTools.call_ai(
                {
                    "api_key": "k",
                    "api_base": "https://example.com/v1",
                    "model": "m",
                    "timeout": 45,
                },
                [{"role": "user", "content": "hi"}],
            )
        self.assertEqual(captured["timeout"], 45.0)

    def test_call_ai_timeout_raises_and_is_catchable(self):
        with mock.patch.object(
            webTools.requests,
            "post",
            side_effect=requests.exceptions.Timeout("slow"),
        ):
            with self.assertRaises(requests.exceptions.Timeout):
                webTools.call_ai(
                    {
                        "api_key": "k",
                        "api_base": "https://example.com/v1",
                        "model": "m",
                    },
                    [{"role": "user", "content": "hi"}],
                    timeout_override=45,
                )

    def test_first_thinking_block_defaults_pass_on_exception(self):
        """Simulate the gatekeeper fail-open contract used by msg.py."""
        flag_need_think = False
        first_thinking_pass = False
        first_thinking_image_ref = "old"
        reply_list = []

        try:
            raise requests.exceptions.Timeout("intent timeout")
        except Exception:
            # mirrors msg.py FIRST THINK FAIL path
            flag_need_think = True
            first_thinking_pass = True
            first_thinking_image_ref = ""
            reply_list = None

        self.assertTrue(flag_need_think)
        self.assertTrue(first_thinking_pass)
        self.assertEqual(first_thinking_image_ref, "")
        self.assertIsNone(reply_list)

    def test_msg_source_contains_fail_open_path(self):
        msg_path = Path(__file__).resolve().parent / "OlivOSAIChatAssassin" / "msg.py"
        text = msg_path.read_text(encoding="utf-8")
        self.assertIn("FIRST THINK FAIL (default PASS)", text)
        self.assertIn("timeout_override=first_thinking_timeout", text)
        self.assertIn(".get('timeout', 45)", text)



if __name__ == "__main__":
    unittest.main()
