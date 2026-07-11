#!/usr/bin/env python3
"""Regression tests for a real 12-message group-chat context."""

import os
import unittest

import OlivOSAIChatAssassin


CHAT = [
    ('20:28:59', '小小怪将军', '灵魂武器碎了再召唤要多少蓝来着'),
    ('20:29:20', '雨多落为萁', '自己翻规则书'),
    ('20:29:22', '雨多落为萁', '有写'),
    ('20:29:27', '小小怪将军', '4 点，6 点，8 点这样加么？'),
    ('20:29:39', '[]', '2468这样'),
    ('20:29:43', '雨多落为萁', '我记得在同一场景里碎了再召唤要+2'),
    ('20:29:43', '小小怪将军', 'ok'),
    ('20:29:51', '小小怪将军', '我现在不太方便开规则书'),
    ('20:29:53', '雨多落为萁', '同一场景才要加'),
    ('20:29:53', '[]', '@Fronia芙萝妮娅3 小芙你闭嘴，你说错了'),
    ('20:29:58', '坟上书', '召唤灵魂武器不耗蓝的来了'),
    ('20:30:35', '雨多落为萁', '@ob_Desom-fu 怎么小芙还嘴硬的'),
    ('20:30:39', '雨多落为萁', '*憋笑'),
    ('20:30:45', '坟上书', '我说自己去看规则书，再看5遍'),
    ('20:31:10', '[]', '@Fronia芙萝妮娅3 还敢嘴硬，罚你去翻规则书'),
    ('20:31:19', 'Desom-fu', '小芙，仔细翻翻狩魂者trpg里面灵魂武器这一段'),
    ('20:31:33', 'Desom-fu', '还在嘴硬'),
    ('20:32:01', 'Desom-fu', '小芙，仔细翻阅狩魂者trpg规则书里面灵魂武器片段，详细说明出来'),
    ('20:32:07', '坟上书', '你翻出来结果了吗（）'),
    ('20:32:09', '雨多落为萁', '戴斯蒙抽小芙.jpg'),
    ('20:32:38', '雨多落为萁', '“你要是不把规则书背完晚上就没有饭吃”的母女既视感'),
    ('20:32:44', 'Desom-fu', '小芙人呢，翻书'),
    ('20:33:09', '雨多落为萁', '孩子悄悄抹眼泪不敢说话'),
    ('20:33:10', '雨多落为萁', 'XD'),
    ('20:33:37', 'Desom-fu', '小芙，仔细念召唤灵魂武器那一段'),
    ('20:34:29', 'Desom-fu', '？'),
    ('20:34:53', '[]', '怎么还+2加骰'),
    ('20:35:00', '几只青桔（苏米）', '？'),
    ('20:35:04', '禺日居士', '小芙，你真的看了规则书吗'),
    ('20:35:12', '雨多落为萁', '开抽'),
    ('20:35:29', '坟上书', '小芙也成神人了'),
    ('20:35:38', '雨多落为萁', '小芙今晚你没背完规则书就不准睡觉！你主人说的'),
    ('20:35:42', '186', '源头找到了'),
    ('20:35:44', '坟上书', '现在是汴京环节吗（）'),
    ('20:36:08', 'Aliar', '小芙'),
    ('20:36:19', 'Aliar', '有点变态'),
    ('20:36:23', 'Fronia芙萝妮娅3', 'Aliar在叫小芙吗～有什么事呀？'),
    ('20:36:25', '幻紫仙门', '这个渴望咆哮是不能选择防御or反击么'),
    ('20:36:28', 'Salieri', '小芙，我多嘴问一句，你读了规则书吗'),
    ('20:36:33', '幻紫仙门', '只能过属性检定'),
    ('20:36:35', '禺日居士', '小芙，我灵能力激活2标签选择5个目标'),
    ('20:36:39', '禺日居士', '要'),
    ('20:36:41', '禺日居士', '多少MP'),
    ('20:37:04', '幻紫仙门', '主要现在有个pc选了后序，他问他有没有后手的1加骰'),
    ('20:37:05', '禺日居士', '@幻紫仙门 就是过属性鉴定吧'),
    ('20:37:10', '禺日居士', '@幻紫仙门 没有'),
    ('20:37:15', '幻紫仙门', '好'),
    ('20:37:22', '坟上书', '那我考你，战斗轮双方对抗检定的成功等级怎么计算？'),
    ('20:37:22', '陆溟', '@幻紫仙门 对属性强度检定都只能检定'),
    ('20:37:23', '禺日居士', '属性和防御不是一码事'),
    ('20:37:27', '幻紫仙门', '那不显得他后手很憨（x'),
    ('20:37:28', '陆溟', '你可以认为是豁免'),
    ('20:37:32', '陆溟', '过豁免'),
    ('20:37:36', '禺日居士', '@幻紫仙门 兑'),
    ('20:37:44', '禺日居士', '狩魂者后手就是憋'),
    ('20:37:49', '禺日居士', ''),
    ('20:38:22', '禺日居士', '小芙，我灵能力激活2标签选择5个目标要消耗几点灵力'),
    ('20:38:31', 'KKKKKK', '其实写是憨@禺日居士'),
    ('20:38:44', '燕尘', '？'),
    ('20:38:45', 'Desom-fu', '？'),
    ('20:38:47', '陆溟', '？'),
    ('20:38:47', '雨多落为萁', '？'),
    ('20:38:57', '雨多落为萁', '孩子是'),
    ('20:39:03', '[]', '？'),
    ('20:39:03', '陆溟', '小芙真好 还送你30点灵力'),
    ('20:39:03', '雨多落为萁', '弱智？！'),
    ('20:39:06', '禺日居士', '小芙，你现在立刻给我去看一遍灵能力选择目标那里，再重新说'),
    ('20:39:10', '[]', '小芙是好狐狸'),
    ('20:39:13', '雨多落为萁', '@燕尘 你的那个弱智表情包呢'),
    ('20:39:14', '雨多落为萁', 'XD'),
    ('20:39:24', '燕尘', '什么弱智表情包'),
    ('20:39:24', 'Desom-fu', '小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分'),
]


class ConfigStub:
    def getConfig(self, _bot_hash):
        return {
            'history_size': 12,
            'skills_enable': True,
            'skills_max_chars': 2000,
            'skills_max_matches': 2,
        }


class RealChat12ContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = OlivOSAIChatAssassin.skillManager
        OlivOSAIChatAssassin.data.gSkillsDir = os.path.expanduser('~/.codex/skills')
        OlivOSAIChatAssassin.data.gSkillsExtraDirs = []
        OlivOSAIChatAssassin.data.gData = ConfigStub()
        OlivOSAIChatAssassin.data.gSkillsIndex = cls.manager.build_skills_index()
        cls.history = [
            {'time': time, 'nickname': nickname, 'message': message}
            for time, nickname, message in CHAT
        ]

    def index_of(self, message):
        return next(index for index, item in enumerate(self.history) if item['message'] == message)

    def selection_at(self, message):
        index = self.index_of(message)
        return self.manager.explain_skill_selection(self.history[: index + 1], 'real-chat')

    def context_at(self, message):
        index = self.index_of(message)
        self.manager.clear_cache()
        return self.manager.get_skills_context(self.history[: index + 1], 'real-chat')

    def test_all_valuable_questions_route_to_shouhunzhe(self):
        questions = [
            '灵魂武器碎了再召唤要多少蓝来着',
            '小芙，仔细翻翻狩魂者trpg里面灵魂武器这一段',
            '小芙，仔细翻阅狩魂者trpg规则书里面灵魂武器片段，详细说明出来',
            '小芙，仔细念召唤灵魂武器那一段',
            '这个渴望咆哮是不能选择防御or反击么',
            '多少MP',
            '那我考你，战斗轮双方对抗检定的成功等级怎么计算？',
            '小芙，我灵能力激活2标签选择5个目标要消耗几点灵力',
            '小芙，你现在立刻给我去看一遍灵能力选择目标那里，再重新说',
            '小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分',
        ]
        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(
                    [item['skill'] for item in self.selection_at(question)],
                    ['shouhunzhe-trpg'],
                )

    def test_rolling_window_is_exactly_twelve_messages(self):
        message = '小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分'
        index = self.index_of(message)
        keywords = self.manager.extract_context_keywords(
            self.history[: index + 1],
            context_messages=12,
        )
        self.assertIn('灵能力多选', keywords)
        self.assertNotIn('灵魂武器', keywords)

    def test_injected_content_contains_question_terms(self):
        cases = {
            '灵魂武器碎了再召唤要多少蓝来着': '灵魂武器',
            '这个渴望咆哮是不能选择防御or反击么': '反击',
            '那我考你，战斗轮双方对抗检定的成功等级怎么计算？': '成功等级',
            '小芙，我灵能力激活2标签选择5个目标要消耗几点灵力': '目标',
            '小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分': '目标',
        }
        for question, expected_text in cases.items():
            with self.subTest(question=question):
                self.assertIn(expected_text, self.context_at(question))


if __name__ == '__main__':
    unittest.main(verbosity=2)
