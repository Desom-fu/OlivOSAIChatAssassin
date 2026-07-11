#!/usr/bin/env python3
"""test_real_queries.py - 真实 memory.json + 真实 skills 多查询测试"""
import sys
import os
import json
import time
import importlib.util

if __name__ != '__main__':
    import unittest
    raise unittest.SkipTest('legacy manual benchmark script')

PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "OlivOSAIChatAssassin")
SKILLS_DIR = r"C:\Users\Administrator\.codex\skills"
MEMORY_FILE = os.path.join(
    r"C:\Users\Administrator\OneDrive\0跑团相关\OlivOS青果相关\青果插件\刺客",
    "memory_d12be72fa32044d6cd139ccd44bb41aa.json"
)

spec = importlib.util.spec_from_file_location(
    "skillManagerV2", os.path.join(PLUGIN_DIR, "skillManagerV2.py")
)
skillManager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skillManager)

import OlivOSAIChatAssassin
tools = OlivOSAIChatAssassin.tools

# 确保配置正确
OlivOSAIChatAssassin.data.gSkillsDir = SKILLS_DIR
if not hasattr(OlivOSAIChatAssassin.data, 'gData') or OlivOSAIChatAssassin.data.gData is None:
    OlivOSAIChatAssassin.data.gData = type('DM', (), {})()
if not hasattr(OlivOSAIChatAssassin.data.gData, 'getConfig'):
    OlivOSAIChatAssassin.data.gData._config = {
        'test_bot': {
            'skills_enable': True,
            'skills_max_chars': 2000,
            'skills_max_matches': 2,
            'skills_match_rate': 0.12,
            'search_ageing': 900,
        }
    }
    OlivOSAIChatAssassin.data.gData.getConfig = lambda bh: OlivOSAIChatAssassin.data.gData._config.get(bh, {})
if not hasattr(OlivOSAIChatAssassin.data.gData, 'getMemory'):
    OlivOSAIChatAssassin.data.gData._memory = {}
    OlivOSAIChatAssassin.data.gData.getMemory = lambda bh: OlivOSAIChatAssassin.data.gData._memory.get(bh, {})

# 确保 configDefault 有必要的字段
for k, v in {
    'skills_enable': True,
    'skills_max_chars': 2000,
    'skills_max_matches': 2,
    'skills_match_rate': 0.12,
    'search_ageing': 900,
}.items():
    if not hasattr(OlivOSAIChatAssassin.data, 'configDefault'):
        OlivOSAIChatAssassin.data.configDefault = {}
    OlivOSAIChatAssassin.data.configDefault.setdefault(k, v)

# 构建 gSkillsIndex
OlivOSAIChatAssassin.data.gSkillsIndex = skillManager.build_skills_index()
print(f"索引完成: {len(OlivOSAIChatAssassin.data.gSkillsIndex)} 个技能")
for name in OlivOSAIChatAssassin.data.gSkillsIndex:
    print(f"  - {name}")

# ============================================================
# 1. peak_up 性能测试
# ============================================================
def benchmark_peakup():
    print("\n" + "=" * 70)
    print("1. peak_up 性能测试 (真实 memory.json, 4483 条知识缓存)")
    print("=" * 70)

    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        mem_data = json.load(f)
    knowledge = mem_data.get('全局', {}).get('知识缓存', {})
    print(f"  知识缓存: {len(knowledge)} 条")

    history = [
        {"nickname": "野生小卒", "user_id": "3251605531", "message": "大家好啊，今天来跑团吧"},
        {"nickname": "键盘英雄", "user_id": "2387419157", "message": "我想玩狩魂者"},
        {"nickname": "Zeroyume", "user_id": "527910600", "message": "狩魂者trpg里面的反击规则是什么？"},
        {"nickname": "Desom-fu", "user_id": "121096913", "message": "反击怎么判定？"},
        {"nickname": "创世", "user_id": "1378451120", "message": "灵能力怎么创建？"},
        {"nickname": "野生小卒", "user_id": "3251605531", "message": "灵魂武器有什么用"},
        {"nickname": "键盘英雄", "user_id": "2387419157", "message": "d20检定怎么算"},
        {"nickname": "Zeroyume", "user_id": "527910600", "message": "战斗先攻怎么决定"},
    ]

    N_CAT = 3
    RATE = 0.1

    # 旧逻辑
    tools.get_recommendRank.cache_clear()
    t0 = time.perf_counter()
    old_calls = 0
    for cat in range(N_CAT):
        for msg in history:
            target = f"{msg['nickname']}({msg['user_id']}): {msg['message']}"
            for k in knowledge:
                old_calls += 1
                rank = tools.get_recommendRank(k, target, rate=RATE)
                tools.get_recommendMatch(rank)
    t1 = time.perf_counter()
    old_time = t1 - t0

    # 新逻辑
    tools.get_recommendRank.cache_clear()
    t0 = time.perf_counter()
    new_calls = 0
    skipped = 0
    for cat in range(N_CAT):
        for msg in history:
            target = f"{msg['nickname']}({msg['user_id']}): {msg['message']}"
            target_lower = target.lower()
            t_bigrams = set()
            if len(target_lower) >= 2:
                for i in range(len(target_lower) - 1):
                    t_bigrams.add(target_lower[i:i+2])
            for k in knowledge:
                k_lower = k.lower()
                if len(k_lower) >= 2:
                    hit = False
                    for i in range(len(k_lower) - 1):
                        if k_lower[i:i+2] in t_bigrams:
                            hit = True
                            break
                    if not hit:
                        skipped += 1
                        continue
                new_calls += 1
                rank = tools.get_recommendRank(k, target, rate=RATE)
                tools.get_recommendMatch(rank)
    t1 = time.perf_counter()
    new_time = t1 - t0

    total = N_CAT * len(history) * len(knowledge)
    skip_rate = skipped / total * 100 if total > 0 else 0
    speedup = old_time / new_time if new_time > 0 else float('inf')

    print(f"\n  旧逻辑: {old_time:.3f}s  (DP {old_calls} 次)")
    print(f"  新逻辑: {new_time:.3f}s  (DP {new_calls} 次, 跳过 {skipped} 次)")
    print(f"  加速比: {speedup:.1f}x")
    print(f"  预筛跳过率: {skip_rate:.1f}%")
    print(f"  预估完整(含deepin): 旧 {old_time*2:.1f}s → 新 {new_time*2:.1f}s")

    ci = tools.get_recommendRank.cache_info()
    print(f"  lru_cache: hits={ci.hits} misses={ci.misses} size={ci.currsize}")


# ============================================================
# 2. 技能匹配测试
# ============================================================
def test_queries():
    print("\n" + "=" * 70)
    print("2. 真实技能多查询测试")
    print("=" * 70)

    base_history = [
        {"message": "大家好啊", "nickname": "群友A", "user_id": "10001", "time": "2026-07-06 10:00:00"},
        {"message": "今天天气不错", "nickname": "群友B", "user_id": "10002", "time": "2026-07-06 10:01:00"},
        {"message": "晚上好呀", "nickname": "群友C", "user_id": "10003", "time": "2026-07-06 10:02:00"},
        {"message": "最近在忙什么", "nickname": "群友A", "user_id": "10001", "time": "2026-07-06 10:03:00"},
        {"message": "随便聊聊", "nickname": "群友B", "user_id": "10002", "time": "2026-07-06 10:04:00"},
    ]

    queries = [
        # 狩魂者 (简体)
        ("狩魂者-简体", "小芙，你能找一下狩魂者trpg里面我应该如何创建灵能力？",
         "shouhunzhe-trpg", ["灵能力"]),
        ("狩魂者-简体", "小芙，找一下吴焰是谁",
         "shouhunzhe-trpg", ["吴焰"]),
        ("狩魂者-简体", "小芙，什么样的灵能力算合规？",
         "shouhunzhe-trpg", ["灵能力"]),
        ("狩魂者-简体", "小芙，我的负面标签能有两个吗？能不能有更多？",
         "shouhunzhe-trpg", ["灵能力标签"]),
        ("狩魂者-简体", "小芙，灵魂武器怎么用？",
         "shouhunzhe-trpg", ["灵魂武器"]),
        ("狩魂者-简体", "小芙，行动检定怎么判？",
         "shouhunzhe-trpg", ["行动检定"]),
        # 狩魂者 (繁体)
        ("狩魂者-繁体", "小芙，找一下狩魂者TRPG裡面我應該如何創建靈能力？",
         "shouhunzhe-trpg", ["灵能力"]),
        ("狩魂者-繁体", "小芙，吳焰是誰？",
         "shouhunzhe-trpg", None),
        ("狩魂者-繁体", "小芙，什麼樣的靈能力算合規？",
         "shouhunzhe-trpg", ["灵能力"]),
        ("狩魂者-繁体", "小芙，我的負面標籤能有兩個嗎？還能更多嗎？",
         "shouhunzhe-trpg", ["灵能力标签"]),
        ("狩魂者-繁体", "小芙，靈魂武器怎麼用？",
         "shouhunzhe-trpg", ["灵魂武器"]),
        # 狩魂者 (英文)
        ("狩魂者-英文", "Xiao Fu, how to create spiritual ability in Shouhunzhe TRPG?",
         "shouhunzhe-trpg", None),
        ("狩魂者-英文", "Who is Wu Yan in Shouhunzhe?",
         "shouhunzhe-trpg", None),
        ("狩魂者-英文", "What makes a spiritual ability valid in Shouhunzhe TRPG?",
         "shouhunzhe-trpg", None),
        ("狩魂者-英文", "Can my spiritual ability have two negative tags or even more?",
         "shouhunzhe-trpg", None),
        ("狩魂者-英文", "How do action checks work in Shouhunzhe TRPG?",
         "shouhunzhe-trpg", None),
        ("狩魂者-英文", "How does the soul weapon work in Shouhunzhe TRPG?",
         "shouhunzhe-trpg", None),
        # OlivOS 插件 (中文)
        ("OlivOS-中文", "小芙，编写olivos插件需要哪些注意事项？",
         "olivos-plugin-developer", None),
        ("OlivOS-中文", "小芙，app.json应该怎么写？",
         "olivos-plugin-developer", ["app.json"]),
        ("OlivOS-中文", "小芙，有没有什么上报来的事件？",
         "olivos-plugin-developer", None),
        ("OlivOS-中文", "小芙，message mode 有哪些？",
         "olivos-plugin-developer", None),
        # OlivOS (繁体)
        ("OlivOS-繁体", "小芙，編寫olivos插件需要哪些注意事項？",
         "olivos-plugin-developer", None),
        ("OlivOS-繁体", "小芙，app.json 應該怎麼寫？",
         "olivos-plugin-developer", ["app.json"]),
        ("OlivOS-繁体", "小芙，message mode 有哪些？",
         "olivos-plugin-developer", None),
        # OlivOS (英文)
        ("OlivOS-英文", "What should I pay attention to when developing an OlivOS plugin?",
         "olivos-plugin-developer", None),
        ("OlivOS-英文", "How to write app.json for an OlivOS plugin?",
         "olivos-plugin-developer", ["app.json"]),
        ("OlivOS-英文", "What events are reported in OlivOS?",
         "olivos-plugin-developer", None),
        ("OlivOS-英文", "What incoming events does OlivOS report to plugins?",
         "olivos-plugin-developer", None),
        ("OlivOS-英文", "What message modes does OlivOS support for plugins?",
         "olivos-plugin-developer", None),
    ]

    passed = 0
    failed = 0

    for category, query, expected_skill, expected_keywords in queries:
        history = base_history + [{
            "message": query,
            "nickname": "提问者",
            "user_id": "99999",
            "time": "2026-07-06 10:05:00"
        }]

        skillManager.clear_cache()

        t0 = time.perf_counter()
        result = skillManager.get_skills_context(history, 'test_bot')
        t1 = time.perf_counter()
        elapsed = t1 - t0

        matched = expected_skill in result
        kw_results = []
        kw_all_found = True
        if expected_keywords:
            for kw in expected_keywords:
                found = kw in result
                kw_results.append(f"{kw}={'Y' if found else 'N'}")
                if not found:
                    kw_all_found = False

        ok = matched and kw_all_found
        if ok:
            passed += 1
        else:
            failed += 1

        status = "PASS" if ok else "FAIL"
        kw_str = f" [{', '.join(kw_results)}]" if expected_keywords else ""
        print(f"  [{status}] [{category}] {elapsed:.3f}s {len(result)}ch{kw_str}")
        print(f"         Q: {query[:65]}")
        if not matched:
            if "[Skill:" in result:
                lines = [l.strip() for l in result.split('\n') if l.strip().startswith('[Skill:')]
                print(f"         Actual: {lines}")
            else:
                print(f"         No skill matched. Preview: {result[:150]}")
        print()

    print("-" * 70)
    print(f"  技能匹配: {passed} 通过, {failed} 失败 / {passed + failed} 总计")
    return passed, failed


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("真实 memory.json + 真实 skills 综合测试")
    print("=" * 70)

    benchmark_peakup()
    passed, failed = test_queries()

    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)
    print(f"  技能匹配: {passed} 通过, {failed} 失败")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)
