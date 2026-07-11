import json
import tempfile
import unittest
from pathlib import Path

from OlivOSAIChatAssassin import load


def make_memory(keys):
    return {
        '全局': {
            '知识缓存': {key: f'value-{key}' for key in keys},
        },
    }


class KnowledgeCacheLimitTests(unittest.TestCase):
    def test_zero_means_unlimited(self):
        memory = make_memory(['a', 'b', 'c'])
        removed = load.trim_knowledge_cache(memory, {'knowledge_cache_max': 0})

        self.assertEqual(removed, [])
        self.assertEqual(list(memory['全局']['知识缓存']), ['a', 'b', 'c'])

    def test_oldest_entries_are_removed(self):
        memory = make_memory(['a', 'b', 'c', 'd'])
        removed = load.trim_knowledge_cache(memory, {'knowledge_cache_max': 2})

        self.assertEqual(removed, ['a', 'b'])
        self.assertEqual(list(memory['全局']['知识缓存']), ['c', 'd'])

    def test_updated_entry_becomes_recent(self):
        memory = make_memory(['a', 'b', 'c'])
        removed = load.update_knowledge_cache(
            memory,
            {'a': 'value-a-new', 'd': 'value-d'},
            {'knowledge_cache_max': 3},
        )

        self.assertEqual(removed, ['b'])
        self.assertEqual(list(memory['全局']['知识缓存']), ['c', 'a', 'd'])
        self.assertEqual(memory['全局']['知识缓存']['a'], 'value-a-new')

    def test_invalid_limit_is_unlimited(self):
        memory = make_memory(['a', 'b', 'c'])
        removed = load.trim_knowledge_cache(memory, {'knowledge_cache_max': 'invalid'})

        self.assertEqual(removed, [])
        self.assertEqual(len(memory['全局']['知识缓存']), 3)

    def test_existing_memory_is_trimmed_when_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            (root_path / 'config_test-bot.json').write_text(
                json.dumps({'knowledge_cache_max': 2}),
                encoding='utf-8',
            )
            (root_path / 'memory_test-bot.json').write_text(
                json.dumps(make_memory(['a', 'b', 'c', 'd'])),
                encoding='utf-8',
            )
            manager = load.DataManager(
                config_dir=root,
                memory_dir=root,
                default_config={'knowledge_cache_max': 0},
                default_memory=make_memory([]),
            )

            manager.load_bot('test-bot')

            self.assertEqual(
                list(manager.getMemory('test-bot')['全局']['知识缓存']),
                ['c', 'd'],
            )


if __name__ == '__main__':
    unittest.main()
