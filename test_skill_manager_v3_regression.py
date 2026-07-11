import os
import inspect
import tempfile
import time
from pathlib import Path
from unittest import mock
import unittest

import OlivOSAIChatAssassin
from test_real_chat_12_context import CHAT


class ConfigStub:
    def getConfig(self, _bot_hash):
        return {'history_size': 12, 'skills_enable': True, 'skills_max_chars': 3000, 'skills_max_matches': 1}


class SkillManagerV3RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OlivOSAIChatAssassin.data.gSkillsDir = os.path.expanduser('~/.codex/skills')
        OlivOSAIChatAssassin.data.gSkillsExtraDirs = []
        OlivOSAIChatAssassin.data.gData = ConfigStub()
        OlivOSAIChatAssassin.data.gSkillsIndex = OlivOSAIChatAssassin.skillManager.build_skills_index()
        cls.history = [{'time': t, 'nickname': n, 'message': m} for t, n, m in CHAT]

    def trace(self, message):
        index = next(i for i, item in enumerate(self.history) if item['message'] == message)
        return OlivOSAIChatAssassin.skillManager.retrieve_skill_context(self.history[:index + 1], 'test')

    def test_real_questions(self):
        expected = {
            '灵魂武器碎了再召唤要多少蓝来着': '灵魂武器',
            '这个渴望咆哮是不能选择防御or反击么': '反击',
            '那我考你，战斗轮双方对抗检定的成功等级怎么计算？': '成功等级',
            '小芙，我灵能力激活2标签选择5个目标要消耗几点灵力': '目标',
            '小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分': '目标',
        }
        for question, keyword in expected.items():
            with self.subTest(question=question):
                trace = self.trace(question)
                self.assertEqual(trace['selections'][0]['skill'], 'shouhunzhe-trpg')
                self.assertIn(keyword, '\n'.join(chunk['text'] for chunk in trace['chunks']))

    def test_history_is_standard_tail(self):
        trace = self.trace('小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分')
        self.assertEqual(trace['history'], self.history[-12:])

    def test_implementation_has_no_domain_or_case_routing_constants(self):
        source = inspect.getsource(OlivOSAIChatAssassin.skillManager)
        forbidden = ('shouhunzhe-trpg\' and', '灵魂武器\',', '灵能力多选\',', 'skill-creator\' and')
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_offline_cross_language_aliases_are_data_driven(self):
        with tempfile.TemporaryDirectory() as root:
            skill_dir = Path(root) / 'demo-rule'
            skill_dir.mkdir()
            (skill_dir / 'SKILL.md').write_text(
                '---\nname: demo-rule\ndescription: 中文规则技能\n'
                'aliases:\n  - spiritual ability\n  - negative tags\n---\n# 规则\n正文',
                encoding='utf-8',
            )
            old_dir = OlivOSAIChatAssassin.data.gSkillsDir
            old_index = OlivOSAIChatAssassin.data.gSkillsIndex
            try:
                OlivOSAIChatAssassin.data.gSkillsDir = root
                OlivOSAIChatAssassin.data.gSkillsIndex = OlivOSAIChatAssassin.skillManager.build_skills_index()
                selected = OlivOSAIChatAssassin.skillManager.explain_skill_selection(
                    [{'message': 'Can my spiritual ability have negative tags?'}],
                    'test',
                )
                self.assertEqual(selected[0]['skill'], 'demo-rule')
            finally:
                OlivOSAIChatAssassin.data.gSkillsDir = old_dir
                OlivOSAIChatAssassin.data.gSkillsIndex = old_index
                OlivOSAIChatAssassin.skillManager.clear_cache()

    def test_skills_max_matches_is_effective(self):
        with tempfile.TemporaryDirectory() as root:
            for name, extra in (('alpha', '甲'), ('beta', '乙'), ('noise', '天气')):
                skill_dir = Path(root) / name
                skill_dir.mkdir()
                (skill_dir / 'SKILL.md').write_text(
                    f'---\nname: {name}\ndescription: 共同检索主题 {extra}\n---\n# {name}\n共同检索主题 {extra}',
                    encoding='utf-8',
                )
            old_dir = OlivOSAIChatAssassin.data.gSkillsDir
            old_index = OlivOSAIChatAssassin.data.gSkillsIndex
            old_data = OlivOSAIChatAssassin.data.gData
            try:
                OlivOSAIChatAssassin.data.gSkillsDir = root
                OlivOSAIChatAssassin.data.gSkillsIndex = OlivOSAIChatAssassin.skillManager.build_skills_index()
                OlivOSAIChatAssassin.data.gData = type(
                    'Config',
                    (),
                    {'getConfig': lambda _self, _bot: {
                        'history_size': 12,
                        'skills_max_matches': 2,
                        'skills_match_rate': 0.01,
                    }},
                )()
                selected = OlivOSAIChatAssassin.skillManager.explain_skill_selection(
                    [{'message': '共同检索主题'}],
                    'test',
                )
                self.assertEqual(len(selected), 2)
                self.assertEqual({item['skill'] for item in selected}, {'alpha', 'beta'})
            finally:
                OlivOSAIChatAssassin.data.gSkillsDir = old_dir
                OlivOSAIChatAssassin.data.gSkillsIndex = old_index
                OlivOSAIChatAssassin.data.gData = old_data
                OlivOSAIChatAssassin.skillManager.clear_cache()

    def test_translation_has_hard_timeout(self):
        old_timeout = OlivOSAIChatAssassin.data.gSkillsTranslationTimeout
        old_cache_path = OlivOSAIChatAssassin.data.gSkillsTranslationCachePath
        with tempfile.TemporaryDirectory() as root:
            try:
                OlivOSAIChatAssassin.data.gSkillsTranslationTimeout = 0.1
                OlivOSAIChatAssassin.data.gSkillsTranslationCachePath = str(Path(root) / 'translation.json')
                OlivOSAIChatAssassin.skillManager._translation_cache.clear()
                with mock.patch.object(
                    OlivOSAIChatAssassin.skillManager.translators,
                    'translate_text',
                    side_effect=lambda *args, **kwargs: time.sleep(2),
                ):
                    started = time.perf_counter()
                    result = OlivOSAIChatAssassin.skillManager._translate_foreign_query(
                        'hard timeout unique translation query',
                    )
                    elapsed = time.perf_counter() - started
                self.assertEqual(result, '')
                self.assertLess(elapsed, 0.5)
            finally:
                OlivOSAIChatAssassin.data.gSkillsTranslationTimeout = old_timeout
                OlivOSAIChatAssassin.data.gSkillsTranslationCachePath = old_cache_path
                OlivOSAIChatAssassin.skillManager._translation_cache.clear()


if __name__ == '__main__':
    unittest.main(verbosity=2)
