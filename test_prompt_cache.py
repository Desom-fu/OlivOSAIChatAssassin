import json
import unittest

from OlivOSAIChatAssassin import data, load, tools
from test_real_chat_12_context import CHAT


def measure_real_chat_prefix(max_grow: int) -> tuple[float, int]:
    """用真实群聊构造连续请求，测量相邻请求可复用的精确 UTF-8 前缀。"""
    queue = tools.DynamicQueue(keep=12, max_grow=max_grow)
    system_prompt = '# 群聊刺客稳定系统提示\n' + ('稳定规则和人格。' * 180)
    previous = None
    total_input = 0
    total_reusable = 0
    rollovers = 0

    for index, (time_text, nickname, message) in enumerate(CHAT):
        previous_size = len(queue)
        queue.append({
            'timestamp': 1783770000 + index,
            'time': f'2026-07-11T{time_text}+08:00',
            'user_id': str(100000 + index % 11),
            'nickname': nickname,
            'message': message,
        })
        if previous_size == max_grow and len(queue) == 12:
            rollovers += 1
        messages = [{'role': 'system', 'content': system_prompt}]
        messages.extend(
            {'role': 'user', 'content': json.dumps(item, ensure_ascii=False)}
            for item in queue
        )
        messages.append({
            'role': 'user',
            'content': json.dumps({
                '当前上下文': {
                    '当前本地时间': f'2026-07-11 20:{index:02d}:00',
                },
                '当前记忆': f'动态摘要-{index}',
                '技能片段': f'动态技能片段-{index}',
            }, ensure_ascii=False),
        })
        payload = json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if index >= 5:
            total_input += len(payload)
            if previous is not None:
                common = 0
                for old_byte, new_byte in zip(previous, payload):
                    if old_byte != new_byte:
                        break
                    common += 1
                total_reusable += common
        previous = payload
    return total_reusable / total_input, rollovers


class PromptCacheHistoryTests(unittest.TestCase):
    def test_cache_optimized_history_grows_before_rollover(self):
        config = {
            'history_size': 3,
            'history_dynamic': False,
            'prompt_cache_optimized': True,
            'prompt_cache_history_size': 6,
        }
        keep, max_grow = load.get_history_limits(config)
        queue = tools.DynamicQueue(keep=keep, max_grow=max_grow)

        for index in range(6):
            queue.append(index)
        self.assertEqual(list(queue), [0, 1, 2, 3, 4, 5])

        queue.append(6)
        self.assertEqual(list(queue), [4, 5, 6])

    def test_cache_optimization_overrides_legacy_fixed_window(self):
        keep, max_grow = load.get_history_limits({
            'history_size': 8,
            'history_dynamic': False,
            'prompt_cache_optimized': True,
            'prompt_cache_history_size': 32,
        })
        self.assertEqual((keep, max_grow), (8, 32))

    def test_disabling_cache_optimization_keeps_legacy_behavior(self):
        keep, max_grow = load.get_history_limits({
            'history_size': 8,
            'history_dynamic': False,
            'prompt_cache_optimized': False,
        })
        self.assertEqual((keep, max_grow), (8, 8))

    def test_invalid_limits_are_safely_normalized(self):
        keep, max_grow = load.get_history_limits({
            'history_size': 'bad',
            'prompt_cache_optimized': True,
            'prompt_cache_history_size': 2,
        })
        self.assertEqual(keep, data.configDefault['history_size'])
        self.assertEqual(max_grow, keep)

    def test_real_chat_prefix_reuse_is_materially_improved(self):
        old_reuse, old_rollovers = measure_real_chat_prefix(12)
        new_reuse, new_rollovers = measure_real_chat_prefix(32)

        self.assertGreater(new_reuse, old_reuse + 0.20)
        self.assertGreater(new_reuse, 0.85)
        self.assertEqual(old_rollovers, 60)
        self.assertEqual(new_rollovers, 2)


if __name__ == '__main__':
    unittest.main()
