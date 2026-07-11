#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_real_skills.py - 使用真实技能测试匹配能力

测试目录: C:\\Users\\Administrator\\.codex\\skills
测试查询: "小芙，狩魂者trpg里面的反击规则是什么？"
"""

import os
import sys

if __name__ != '__main__':
    import unittest
    raise unittest.SkipTest('legacy manual regression script')

# ============================================================
# Mock OlivOSAIChatAssassin 模块结构
# ============================================================

class MockLogger:
    @staticmethod
    def log(msg):
        print(f'  [LOG] {msg}')

    @staticmethod
    def warn(msg):
        print(f'  [WARN] {msg}')


class MockDataManager:
    def __init__(self):
        self.config = {
            'default_bot': {
                'skills_enable': True,
                'skills_max_chars': 2000,
                'skills_max_matches': 2,
                'skills_match_rate': 0.12,
                'search_ageing': 900,
            }
        }
        self.memory = {}

    def getConfig(self, bot_hash):
        return self.config.get(bot_hash, self.config.get('default_bot', {}))

    def getMemory(self, bot_hash):
        return self.memory.get(bot_hash, {})


class MockData:
    def __init__(self):
        self.gSkillsDir = ''
        self.gSkillsIndex = {}
        self.gData = MockDataManager()
        self.configDefault = {
            'skills_enable': True,
            'skills_max_chars': 2000,
            'skills_max_matches': 2,
            'skills_match_rate': 0.12,
            'search_ageing': 900,
        }


class MockTools:
    @staticmethod
    def get_recommendRank(word1_in, word2_in, gate_rank=1000, rate=0.1):
        word1 = word1_in.lower()
        word2 = word2_in.lower()
        if not word1 or not word2:
            return gate_rank + 1
        if len(word1) > len(word2):
            return gate_rank + 2
        len1 = len(word1)
        len2 = len(word2)
        if word2.find(word1) != -1:
            return 0
        prev_lcs = [0] * (len1 + 1)
        prev_ed = list(range(len1 + 1))
        for j in range(1, len2 + 1):
            ch2 = word2[j - 1]
            cur_lcs = [0] * (len1 + 1)
            cur_ed = [0] * (len1 + 1)
            cur_ed[0] = j
            for i in range(1, len1 + 1):
                if word1[i - 1] == ch2:
                    cur_lcs[i] = prev_lcs[i - 1] + 1
                    cur_ed[i] = prev_ed[i - 1]
                else:
                    cur_lcs[i] = max(prev_lcs[i], cur_lcs[i - 1])
                    cur_ed[i] = min(prev_ed[i - 1], prev_ed[i], cur_ed[i - 1]) + 1
            prev_lcs = cur_lcs
            prev_ed = cur_ed
        iRank_1 = prev_lcs[len1]
        iRank_2 = prev_ed[len1]
        iRank = len2 * (len1 - iRank_1) + iRank_2 + 1
        iRank = (iRank * iRank) // len1 // len2
        if iRank >= int(len1 * len2 * rate):
            iRank += gate_rank
        return iRank

    @staticmethod
    def get_recommendMatch(rank, gate_rank=1000):
        return rank < gate_rank


class MockOlivOSAIChatAssassin:
    data = MockData()
    tools = MockTools
    logger = MockLogger


# ============================================================
# 注入 mock 并加载 skillManager
# ============================================================

import importlib.util

mock_module = MockOlivOSAIChatAssassin()
sys.modules['OlivOS'] = type('MockOlivOS', (), {
    'pluginAPI': type('MockAPI', (), {'shallow': None}),
    'API': type('MockAPI2', (), {'Event': type('MockEvent', (), {})}),
})()
sys.modules['OlivOSAIChatAssassin'] = mock_module

skill_manager_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'OlivOSAIChatAssassin',
    'skillManagerV2.py'
)
spec = importlib.util.spec_from_file_location('skillManager', skill_manager_path)
skillManager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skillManager)


# ============================================================
# 真实技能测试
# ============================================================

REAL_SKILLS_DIR = r'C:\Users\Administrator\.codex\skills'

print('=' * 70)
print('真实技能匹配测试')
print(f'技能目录: {REAL_SKILLS_DIR}')
print('=' * 70)

# 1. 检查目录是否存在
if not os.path.isdir(REAL_SKILLS_DIR):
    print(f'[ERROR] 技能目录不存在: {REAL_SKILLS_DIR}')
    sys.exit(1)

# 2. 列出目录内容
print('\n--- 目录结构 ---')
for root, dirs, files in os.walk(REAL_SKILLS_DIR):
    level = root.replace(REAL_SKILLS_DIR, '').count(os.sep)
    indent = '  ' * level
    print(f'{indent}{os.path.basename(root)}/')
    subindent = '  ' * (level + 1)
    for file in files:
        filepath = os.path.join(root, file)
        size = os.path.getsize(filepath)
        print(f'{subindent}{file} ({size} bytes)')

# 3. 构建索引
print('\n--- 构建索引 ---')
mock_module.data.gSkillsDir = REAL_SKILLS_DIR
index = skillManager.build_skills_index()
mock_module.data.gSkillsIndex = index

print(f'\n索引技能数: {len(index)}')
for name, entry in index.items():
    print(f'  - {name}: {len(entry.get("headers", []))} 个标题, {len(entry.get("nav_table", []))} 条导航, {len(entry.get("references", []))} 个引用文件')
    print(f'    描述: {entry.get("description", "")[:100]}')
    if entry.get('headers'):
        print(f'    标题列表:')
        for h in entry['headers']:
            print(f'      [{"#" * h["level"]}] {h["title"]} (行 {h["line"]})')
    if entry.get('nav_table'):
        print(f'    导航表:')
        for nav in entry['nav_table']:
            print(f'      {nav}')

# 4. 测试查询: "小芙，狩魂者trpg里面的反击规则是什么？"
print('\n' + '=' * 70)
print('测试查询: "小芙，狩魂者trpg里面的反击规则是什么？"')
print('=' * 70)

# 模拟聊天历史 (多条消息,最后一条是目标查询)
history = [
    {
        'message': '大家好啊',
        'nickname': '用户A',
        'user_id': '10001',
        'time': '2026-07-06 10:00:00',
    },
    {
        'message': '今天来跑团吧',
        'nickname': '用户B',
        'user_id': '10002',
        'time': '2026-07-06 10:01:00',
    },
    {
        'message': '好啊好啊',
        'nickname': '用户C',
        'user_id': '10003',
        'time': '2026-07-06 10:02:00',
    },
    {
        'message': '我想玩狩魂者trpg',
        'nickname': '用户B',
        'user_id': '10002',
        'time': '2026-07-06 10:03:00',
    },
    {
        'message': '小芙，狩魂者trpg里面的反击规则是什么？',
        'nickname': '用户D',
        'user_id': '10004',
        'time': '2026-07-06 10:04:00',
    },
]

# 提取关键词
print('\n--- 关键词提取 ---')
keywords = skillManager.extract_context_keywords(history)
print(f'关键词 ({len(keywords)} 个): {keywords}')

# 匹配技能
print('\n--- 技能匹配 ---')
matches = skillManager.match_skills(keywords, 'default_bot')
print(f'匹配到 {len(matches)} 个技能:')
for m in matches:
    print(f'  技能: {m["entry"]["name"]}')
    print(f'  分数: {m["score"]}')
    print(f'  命中标题: {[h["title"] for h in m.get("matched_headers", [])]}')
    print(f'  命中导航: {m.get("matched_nav", [])}')

# 完整流程
print('\n--- 完整流程 (get_skills_context) ---')
result = skillManager.get_skills_context(history, 'default_bot')
if result:
    print(f'结果长度: {len(result)} 字符')
    print(f'\n--- 注入内容 ---')
    print(result)
else:
    print('无匹配结果!')

# 5. 验证
print('\n' + '=' * 70)
print('验证结果')
print('=' * 70)

PASS = 0
FAIL = 0

def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}')

check('匹配到至少 1 个技能', len(matches) > 0)
if matches:
    check('匹配到 shouhunzhe-trpg 技能', matches[0]['entry']['name'] == 'shouhunzhe-trpg')
check('结果非空', len(result) > 0)
if result:
    check('包含技能知识头', '# 技能知识' in result)
    check('包含 shouhunzhe-trpg 标记', 'shouhunzhe-trpg' in result or '狩魂者' in result)
    # 检查是否包含反击相关内容
    check('包含"反击"关键词', '反击' in result)
    # 检查是否包含规则书内容
    check('包含规则书引用', 'rulebook' in result.lower() or '规则' in result)

print(f'\n{"=" * 70}')
print(f'真实技能测试结果: {PASS} 通过, {FAIL} 失败')
print(f'{"=" * 70}')

sys.exit(0 if FAIL == 0 else 1)
