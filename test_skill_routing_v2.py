#!/usr/bin/env python3
"""Regression matrix for V2 routing against the installed Codex skills."""

import os
import unittest

import OlivOSAIChatAssassin


SKILLS_DIR = os.path.expanduser('~/.codex/skills')


class ConfigStub:
    def getConfig(self, _bot_hash):
        return {
            'skills_enable': True,
            'skills_max_chars': 2000,
            'skills_max_matches': 2,
        }


class SkillRoutingV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = OlivOSAIChatAssassin.skillManager
        OlivOSAIChatAssassin.data.gSkillsDir = SKILLS_DIR
        OlivOSAIChatAssassin.data.gSkillsExtraDirs = []
        OlivOSAIChatAssassin.data.gData = ConfigStub()
        OlivOSAIChatAssassin.data.gSkillsIndex = cls.manager.build_skills_index()

    def selected(self, *messages):
        history = [{'message': message} for message in messages]
        return [item['skill'] for item in self.manager.explain_skill_selection(history, 'test')]

    def test_installed_skill_inventory(self):
        self.assertEqual(
            set(OlivOSAIChatAssassin.data.gSkillsIndex),
            {
                'imagegen',
                'openai-docs',
                'plugin-creator',
                'skill-creator',
                'skill-installer',
                'olivos-plugin-developer',
                'shouhunzhe-trpg',
            },
        )

    def test_exact_routing_matrix(self):
        cases = {
            'Generate a transparent-background pixel art sprite with the image API.': ['imagegen'],
            'How do I authenticate with the OpenAI Responses API?': ['openai-docs'],
            'Create a Codex plugin with .codex-plugin/plugin.json and a marketplace entry.': ['plugin-creator'],
            'Help me create and validate a reusable Codex skill with SKILL.md.': ['skill-creator'],
            'Install a Codex skill from a GitHub repository.': ['skill-installer'],
            '写一个OlivOS群聊插件，处理group_message事件。': ['olivos-plugin-developer'],
            '狩魂者TRPG里的行动检定怎么判？': ['shouhunzhe-trpg'],
            'Can my spiritual ability have two negative tags or even more?': ['shouhunzhe-trpg'],
            '今天天气真好，晚上吃什么？': [],
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(self.selected(query), expected)

    def test_recent_context_routes_implicit_follow_up(self):
        self.assertEqual(
            self.selected('我们正在聊狩魂者TRPG。', '负面标签可以有两个吗？'),
            ['shouhunzhe-trpg'],
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
