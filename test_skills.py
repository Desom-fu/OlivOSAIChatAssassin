#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_skills.py - OlivOSAIChatAssassin Skills 动态检索系统测试脚本

测试覆盖:
  1. 递归索引: 多层级子目录正确索引
  2. 关键词提取: 中文聊天文本正确分词
  3. 模糊匹配: 关键词命中正确技能
  4. 切片读取: 大文件只返回相关段落
  5. Token 预算: 多技能匹配时不超过上限
  6. 边界情况: 空目录、无 SKILL.md、无匹配
  7. 全流程: get_skills_context 端到端
"""

import os
import sys
import shutil
import tempfile
import json

# ============================================================
# Mock OlivOSAIChatAssassin 模块结构 (避免依赖真实 OlivOS)
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
    """复刻 tools.py 中的 get_recommendRank 和 get_recommendMatch"""

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


# 构建 mock 模块
class MockOlivOSAIChatAssassin:
    data = MockData()
    tools = MockTools
    logger = MockLogger


# ============================================================
# 注入 mock 模块并加载 skillManager
# ============================================================

import importlib.util

# 将 mock 模块注入 sys.modules (在加载 skillManager 之前)
mock_module = MockOlivOSAIChatAssassin()
sys.modules['OlivOS'] = type('MockOlivOS', (), {
    'pluginAPI': type('MockAPI', (), {'shallow': None}),
    'API': type('MockAPI2', (), {'Event': type('MockEvent', (), {})}),
})()
sys.modules['OlivOSAIChatAssassin'] = mock_module

# 直接用 importlib 加载 skillManager.py (绕过包导入)
skill_manager_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'OlivOSAIChatAssassin',
    'skillManager.py'
)
spec = importlib.util.spec_from_file_location('skillManager', skill_manager_path)
skillManager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skillManager)


# ============================================================
# 测试辅助函数
# ============================================================

def create_test_skill(base_dir, skill_name, content):
    """创建测试技能目录和 SKILL.md"""
    skill_dir = os.path.join(base_dir, skill_name)
    os.makedirs(skill_dir, exist_ok=True)
    skill_md = os.path.join(skill_dir, 'SKILL.md')
    with open(skill_md, 'w', encoding='utf-8') as f:
        f.write(content)
    return skill_dir


def make_history(messages):
    """构建模拟聊天历史"""
    return [
        {
            'message': msg,
            'nickname': f'用户{i}',
            'user_id': f'1000{i}',
            'time': f'2026-07-06 10:{i:02d}:00',
        }
        for i, msg in enumerate(messages)
    ]


# ============================================================
# 测试用例
# ============================================================

PASS = 0
FAIL = 0


def assert_eq(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}')
        print(f'    期望: {expected}')
        print(f'    实际: {actual}')


def assert_true(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name} - 条件为假')


def assert_gt(name, actual, threshold):
    global PASS, FAIL
    if actual > threshold:
        PASS += 1
        print(f'  [PASS] {name} ({actual} > {threshold})')
    else:
        FAIL += 1
        print(f'  [FAIL] {name} ({actual} <= {threshold})')


def assert_le(name, actual, threshold):
    global PASS, FAIL
    if actual <= threshold:
        PASS += 1
        print(f'  [PASS] {name} ({actual} <= {threshold})')
    else:
        FAIL += 1
        print(f'  [FAIL] {name} ({actual} > {threshold})')


def test_recursive_index():
    """测试 1: 递归索引"""
    print('\n=== 测试 1: 递归索引 ===')
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir

        # 技能 1: 简单技能
        create_test_skill(tmpdir, 'simple-skill', '''---
name: simple-skill
description: 一个简单的测试技能
---
# 简单技能

## 基本用法
这是一个简单的测试技能。

## 高级用法
高级功能说明。
''')

        # 技能 2: 带子目录和引用文件
        skill2_dir = create_test_skill(tmpdir, 'complex-skill', '''---
name: complex-skill
description: 复杂技能带引用文件和导航表
---
# 复杂技能

## 概述
这是一个复杂技能。

## 资料入口
完整规则书位于 `references/rulebook.md`。

## 逐章导航

| 用户问题 | 读取章节 |
| --- | --- |
| 战斗规则 | 战斗 |
| 角色创建 | 创建角色 |
| 行动检定 | 行动检定 |
''')
        # 创建引用文件
        refs_dir = os.path.join(skill2_dir, 'references')
        os.makedirs(refs_dir, exist_ok=True)
        with open(os.path.join(refs_dir, 'rulebook.md'), 'w', encoding='utf-8') as f:
            f.write('# 规则书\n\n## 战斗\n战斗回合和伤害计算。\n\n## 行动检定\n使用d20检定。\n\n## 创建角色\n角色创建流程。\n')

        # 技能 3: 深层嵌套目录
        deep_dir = os.path.join(tmpdir, 'deep', 'nested', 'path', 'deep-skill')
        os.makedirs(deep_dir, exist_ok=True)
        with open(os.path.join(deep_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
            f.write('''---
name: deep-skill
description: 深层嵌套目录中的技能
---
# 深层技能

## 说明
在深层目录中。
''')

        # 构建索引
        index = skillManager.build_skills_index()

        assert_eq('索引技能数量', len(index), 3)
        assert_true('包含 simple-skill', 'simple-skill' in index)
        assert_true('包含 complex-skill', 'complex-skill' in index)
        assert_true('包含 deep-skill', 'deep-skill' in index)

        # 检查 simple-skill 的标题索引
        simple = index.get('simple-skill', {})
        headers = simple.get('headers', [])
        assert_eq('simple-skill 标题数', len(headers), 2)
        assert_eq('第一个标题', headers[0]['title'] if headers else '', '基本用法')

        # 检查 complex-skill 的导航表
        complex_skill = index.get('complex-skill', {})
        nav = complex_skill.get('nav_table', [])
        assert_eq('导航表条目数', len(nav), 3)

        # 检查 complex-skill 的引用文件
        refs = complex_skill.get('references', [])
        assert_true('引用文件包含 rulebook.md', any('rulebook.md' in r for r in refs))

        # 检查 deep-skill 路径
        deep = index.get('deep-skill', {})
        assert_true('deep-skill 目录存在', os.path.exists(deep.get('dir', '')))

    finally:
        shutil.rmtree(tmpdir)


def test_keyword_extraction():
    """测试 2: 关键词提取"""
    print('\n=== 测试 2: 关键词提取 ===')

    history = make_history([
        '大家好，今天我们来玩狩魂者TRPG吧',
        '我想创建一个新角色，灵能力选什么好',
        '狩魂者的战斗规则是什么',
        '行动检定怎么搞',
        '灵魂武器怎么用',
    ])

    keywords = skillManager.extract_context_keywords(history)

    assert_true('关键词非空', len(keywords) > 0)
    assert_true('包含狩魂者', '狩魂者' in keywords)
    assert_true('包含行动检定', '行动检定' in keywords)
    assert_true('包含灵魂武器', '灵魂武器' in keywords)

    # 测试空历史
    empty_kw = skillManager.extract_context_keywords([])
    assert_eq('空历史关键词', len(empty_kw), 0)

    # 测试纯 media tag 消息
    media_history = make_history([
        '[OP:image,url=https://example.com/img.jpg]',
        '[CQ:face,id=123]',
    ])
    media_kw = skillManager.extract_context_keywords(media_history)
    assert_eq('纯 media tag 关键词', len(media_kw), 0)


def test_skill_matching():
    """测试 3: 模糊匹配"""
    print('\n=== 测试 3: 模糊匹配 ===')
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir

        # 创建 TRPG 技能
        create_test_skill(tmpdir, 'trpg-rules', '''---
name: trpg-rules
description: TRPG规则解读、行动检定、战斗规则、角色创建
---
# TRPG规则

## 行动检定
使用d20进行检定。

## 战斗
回合制战斗规则。

## 角色创建
角色构建流程。
''')

        # 创建无关技能
        create_test_skill(tmpdir, 'cooking-recipes', '''---
name: cooking-recipes
description: 烹饪食谱、菜系介绍、食材搭配
---
# 烹饪指南

## 红烧肉
经典家常菜。

## 清蒸鱼
清淡健康。
''')

        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        # 用 TRPG 相关关键词匹配
        history = make_history([
            '帮我看看行动检定怎么过',
            '战斗回合怎么算',
        ])
        keywords = skillManager.extract_context_keywords(history)
        matches = skillManager.match_skills(keywords, 'default_bot')

        assert_true('匹配到技能', len(matches) > 0)
        assert_eq('匹配到 trpg-rules', matches[0]['entry']['name'] if matches else '', 'trpg-rules')
        assert_gt('trpg-rules 分数', matches[0]['score'] if matches else 0, 0)

        # 检查标题命中
        matched_headers = matches[0].get('matched_headers', [])
        header_titles = [h['title'] for h in matched_headers]
        assert_true('命中行动检定标题', '行动检定' in header_titles)
        assert_true('命中战斗标题', '战斗' in header_titles)

        # 用烹饪相关关键词匹配
        cook_history = make_history(['今天做红烧肉', '清蒸鱼怎么做'])
        cook_kw = skillManager.extract_context_keywords(cook_history)
        cook_matches = skillManager.match_skills(cook_kw, 'default_bot')
        assert_true('匹配到烹饪技能', len(cook_matches) > 0)
        assert_eq('匹配到 cooking-recipes', cook_matches[0]['entry']['name'] if cook_matches else '', 'cooking-recipes')

        # 用无关关键词匹配
        unrelated_history = make_history(['今天天气真好', '出去散步吧'])
        unrelated_kw = skillManager.extract_context_keywords(unrelated_history)
        unrelated_matches = skillManager.match_skills(unrelated_kw, 'default_bot')
        assert_eq('无关关键词无匹配', len(unrelated_matches), 0)

    finally:
        shutil.rmtree(tmpdir)


def test_content_slicing():
    """测试 4: 切片读取"""
    print('\n=== 测试 4: 切片读取 ===')
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir

        # 创建大文件技能
        large_content = '''---
name: large-skill
description: 大文件技能测试
---
# 大文件技能

## 章节1
这是章节1的内容。
''' + '\n'.join([f'这是第{i}行内容。' for i in range(50)]) + '''

## 章节2
这是章节2的内容。
''' + '\n'.join([f'章节2第{i}行。' for i in range(50)]) + '''

## 章节3
这是章节3的内容。
''' + '\n'.join([f'章节3第{i}行。' for i in range(50)]) + '''
'''
        create_test_skill(tmpdir, 'large-skill', large_content)

        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        # 匹配章节2
        match = {
            'entry': index['large-skill'],
            'score': 5,
            'matched_headers': [h for h in index['large-skill']['headers'] if '章节2' in h.get('title', '')],
            'matched_nav': [],
        }

        result = skillManager.slice_skill_content(match, 1000)

        assert_true('切片非空', len(result) > 0)
        assert_true('包含 Skill 标记', '[Skill: large-skill]' in result)
        assert_true('包含章节2内容', '章节2' in result)
        assert_le('切片不超过预算', len(result), 1100)  # 允许少量超出(header)

        # 测试小文件整包读取
        small_content = '''---
name: small-skill
description: 小文件技能
---
# 小技能
内容很短。
'''
        create_test_skill(tmpdir, 'small-skill', small_content)
        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        small_match = {
            'entry': index['small-skill'],
            'score': 3,
            'matched_headers': [],
            'matched_nav': [],
        }
        small_result = skillManager.slice_skill_content(small_match, 2000)
        assert_true('小文件包含全部内容', '内容很短' in small_result)

    finally:
        shutil.rmtree(tmpdir)


def test_token_budget():
    """测试 5: Token 预算控制"""
    print('\n=== 测试 5: Token 预算控制 ===')
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir
        mock_module.data.gData.config['default_bot']['skills_max_chars'] = 500

        # 创建两个技能
        for name, desc in [('skill-a', '技能A描述'), ('skill-b', '技能B描述')]:
            create_test_skill(tmpdir, name, f'''---
name: {name}
description: {desc}
---
# {name}

## 内容
''' + '\n'.join([f'这是{i}行内容需要填充一些文字。' for i in range(30)]) + '\n')

        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        # 用能匹配两个技能的关键词
        history = make_history(['技能A的内容', '技能B的内容'])
        keywords = skillManager.extract_context_keywords(history)

        result = skillManager.get_skills_context(history, 'default_bot')

        if result:
            assert_le('总输出不超过预算+容差', len(result), 600)
            assert_true('包含技能知识标记', '# 技能知识' in result)
        else:
            print('  [INFO] 无匹配结果(可能是关键词不够精确)')

        # 恢复默认
        mock_module.data.gData.config['default_bot']['skills_max_chars'] = 2000

    finally:
        shutil.rmtree(tmpdir)


def test_edge_cases():
    """测试 6: 边界情况"""
    print('\n=== 测试 6: 边界情况 ===')

    # 空目录
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir
        index = skillManager.build_skills_index()
        assert_eq('空目录索引', len(index), 0)

        # 无 SKILL.md 的目录
        os.makedirs(os.path.join(tmpdir, 'no-skill-md'), exist_ok=True)
        with open(os.path.join(tmpdir, 'no-skill-md', 'readme.txt'), 'w') as f:
            f.write('not a skill')
        index = skillManager.build_skills_index()
        assert_eq('无SKILL.md索引', len(index), 0)

        # 畸形 SKILL.md (无 frontmatter)
        bad_dir = os.path.join(tmpdir, 'bad-skill')
        os.makedirs(bad_dir, exist_ok=True)
        with open(os.path.join(bad_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
            f.write('no frontmatter here')
        index = skillManager.build_skills_index()
        assert_eq('畸形SKILL.md索引', len(index), 0)

        # 正常技能
        create_test_skill(tmpdir, 'good-skill', '''---
name: good-skill
description: 正常技能
---
# 正常技能
内容。
''')
        index = skillManager.build_skills_index()
        assert_eq('混合目录索引', len(index), 1)
        assert_true('只索引好的技能', 'good-skill' in index)

    finally:
        shutil.rmtree(tmpdir)

    # 无匹配场景
    tmpdir2 = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir2
        create_test_skill(tmpdir2, 'unrelated-skill', '''---
name: unrelated-skill
description: 完全不相关的技能
---
# 不相关
内容。
''')
        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        history = make_history(['今天天气真好', '出去散步吧'])
        result = skillManager.get_skills_context(history, 'default_bot')
        assert_eq('无匹配返回空', result, '')

    finally:
        shutil.rmtree(tmpdir2)


def test_full_pipeline():
    """测试 7: 全流程端到端"""
    print('\n=== 测试 7: 全流程端到端 ===')
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir
        mock_module.data.gData.config['default_bot']['skills_max_chars'] = 2000
        mock_module.data.gData.config['default_bot']['skills_enable'] = True

        # 创建狩魂者TRPG技能 (模拟真实场景)
        skill_content = '''---
name: shouhunzhe-trpg
description: 狩魂者TRPG中文规则解读、规则重点梳理、建卡辅助、战斗与行动检定裁定、DH跑团主持
---
# 狩魂者TRPG规则与跑团辅助

## 基本原则
先读取相关章节，再回答规则问题。

## 行动检定
使用d20进行检定，挑战值由DH设定。
检定流程：掷d20 + 属性加骰，对比挑战值。
成功等级：大成功、成功、失败、大失败。

## 战斗
回合制战斗，每回合一个行动。
先攻检定：d20 + 敏捷。
伤害计算：武器伤害 + 力量加成。
生命值：体质 * 4 + 起始生命值。

## 灵魂武器
凝练灵魂武器需要灵能力达到要求。
使用灵魂武器消耗灵能力点数。
'''
        create_test_skill(tmpdir, 'shouhunzhe-trpg', skill_content)

        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        # 模拟群聊对话
        history = make_history([
            '今天来跑狩魂者吧',
            '我想用灵魂武器打怪，怎么操作',
            '行动检定的挑战值是多少',
            '战斗回合怎么算',
            '帮我看看战斗规则',
        ])

        result = skillManager.get_skills_context(history, 'default_bot')

        assert_true('全流程结果非空', len(result) > 0)
        assert_true('包含技能知识头', '# 技能知识' in result)
        assert_true('包含技能标记', '[Skill: shouhunzhe-trpg]' in result)
        assert_true('包含战斗内容', '战斗' in result)
        assert_true('包含行动检定', '行动检定' in result or '检定' in result)
        assert_le('全流程结果长度合理', len(result), 2200)

        # 测试禁用技能
        mock_module.data.gData.config['default_bot']['skills_enable'] = False
        disabled_result = skillManager.get_skills_context(history, 'default_bot')
        assert_eq('禁用时返回空', disabled_result, '')
        mock_module.data.gData.config['default_bot']['skills_enable'] = True

    finally:
        shutil.rmtree(tmpdir)


def test_nav_table_slicing():
    """测试 8: 导航表切片 (跨文件引用)"""
    print('\n=== 测试 8: 导航表切片 (跨文件引用) ===')
    tmpdir = tempfile.mkdtemp()
    try:
        mock_module.data.gSkillsDir = tmpdir

        # 创建带导航表和引用文件的技能
        skill_dir = os.path.join(tmpdir, 'nav-skill')
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
            f.write('''---
name: nav-skill
description: 带导航表的技能
---
# 导航技能

## 概述
本技能有导航表。

## 逐章导航

| 用户问题 | 读取章节 |
| --- | --- |
| 战斗规则 | 战斗 |
| 角色创建 | 创建角色 |
| 魔法系统 | 魔法 |
''')

        # 创建引用文件
        refs_dir = os.path.join(skill_dir, 'references')
        os.makedirs(refs_dir, exist_ok=True)
        with open(os.path.join(refs_dir, 'rulebook.md'), 'w', encoding='utf-8') as f:
            f.write('''# 规则书

## 战斗
战斗回合制。先攻检定。
伤害计算。
生命值管理。

## 创建角色
角色创建流程。
属性分配。
技能选择。

## 魔法
魔法系统说明。
法术列表。
施法规则。
''')

        index = skillManager.build_skills_index()
        mock_module.data.gSkillsIndex = index

        # 用"战斗规则"关键词匹配
        history = make_history(['战斗规则是什么', '怎么打怪'])
        result = skillManager.get_skills_context(history, 'default_bot')

        assert_true('导航表匹配结果非空', len(result) > 0)
        assert_true('包含技能标记', '[Skill: nav-skill]' in result)

    finally:
        shutil.rmtree(tmpdir)


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    print('=' * 60)
    print('OlivOSAIChatAssassin Skills 动态检索系统 - 测试套件')
    print('=' * 60)

    test_recursive_index()
    test_keyword_extraction()
    test_skill_matching()
    test_content_slicing()
    test_token_budget()
    test_edge_cases()
    test_full_pipeline()
    test_nav_table_slicing()

    print('\n' + '=' * 60)
    print(f'测试结果: {PASS} 通过, {FAIL} 失败')
    print('=' * 60)

    sys.exit(0 if FAIL == 0 else 1)
