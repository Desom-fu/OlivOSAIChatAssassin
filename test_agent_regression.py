"""agent.py 回归测试：mock OlivOS 环境，验证各组件逻辑正确性。"""

import sys
import os
import json
import types
import time
import threading
from unittest.mock import MagicMock, patch
from collections import deque

# ---------------------------------------------------------------------------
# Mock OlivOS 及 OlivOSAIChatAssassin 包结构
# ---------------------------------------------------------------------------

# 确保插件目录在 path 中
PLUGIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'OlivOSAIChatAssassin')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock OlivOS 顶层模块
olivos_mock = types.ModuleType('OlivOS')
olivos_mock.API = MagicMock()
olivos_mock.pluginAPI = MagicMock()
olivos_mock.contentAPI = MagicMock()
sys.modules['OlivOS'] = olivos_mock
sys.modules['OlivOS.API'] = olivos_mock.API
sys.modules['OlivOS.pluginAPI'] = olivos_mock.pluginAPI
sys.modules['OlivOS.contentAPI'] = olivos_mock.contentAPI

# 先导入 data 模块需要的最小依赖
# 直接 import 整个包会触发 __init__.py 的全部导入链，我们逐步构建
import OlivOSAIChatAssassin.data as data_mod
import OlivOSAIChatAssassin.logger as logger_mod
import OlivOSAIChatAssassin.tools as tools_mod

# Mock skillManager
skill_mock = types.ModuleType('OlivOSAIChatAssassin.skillManager')
skill_mock.get_skills_context = MagicMock(return_value='[Skill: test] 测试技能片段')
sys.modules['OlivOSAIChatAssassin.skillManager'] = skill_mock

# Mock msg 模块的最小接口
msg_mock = types.ModuleType('OlivOSAIChatAssassin.msg')


def _mock_get_ai_context(lConfig, history, content, **kwargs):
    messages = [{'role': 'system', 'content': content}]
    for entry in history:
        role = 'assistant' if entry.get('nickname') is None else 'user'
        messages.append({'role': role, 'content': json.dumps(entry, ensure_ascii=False)})
    patch = kwargs.get('patch')
    if patch:
        messages.append({'role': 'user', 'content': f'当前动态上下文：{json.dumps(patch, ensure_ascii=False)}'})
    return messages


def _mock_get_json_message(data_str):
    try:
        data_dict = json.loads(data_str)
        if isinstance(data_dict, dict) and 'r' in data_dict and isinstance(data_dict['r'], list):
            return data_dict['r']
    except Exception:
        pass
    return None


def _mock_img_handler(entry_data, patch_data):
    return entry_data, patch_data


msg_mock.get_ai_context = _mock_get_ai_context
msg_mock.get_json_message = _mock_get_json_message
msg_mock.img_handler = _mock_img_handler
sys.modules['OlivOSAIChatAssassin.msg'] = msg_mock

# 确保 OlivOSAIChatAssassin 包模块可被 agent.py 引用
import OlivOSAIChatAssassin
OlivOSAIChatAssassin.data = data_mod
OlivOSAIChatAssassin.logger = logger_mod
OlivOSAIChatAssassin.tools = tools_mod
OlivOSAIChatAssassin.msg = msg_mock
OlivOSAIChatAssassin.skillManager = skill_mock

# 设置 data 模块的全局状态
data_mod.gStaticKnowledge = {'Python': '一种高级编程语言', 'TRPG': '桌上角色扮演游戏'}
data_mod.gImageCache = {
    'group_001': deque([
        ('cat.png', {'content': '一只猫', 'intent': '卖萌', 'type': '表情包'}),
        ('dog.jpg', {'content': '一只狗', 'intent': '搞笑', 'type': '图片'}),
    ])
}
data_mod.gMemoryDefaultStr = '择机加入对话'

# Mock DataManager
mock_memory = {
    '全局': {
        '知识缓存': {'天气': '今天晴天', '游戏': '正在玩TRPG'},
        '知识搜索': {},
        '用户侧写': {'12345': '小明：阳光开朗', '67890': '小红：内向文静'},
        '人物关系': {},
        '图片缓存': {},
    },
    'group_001': '刚才在聊TRPG规则',
}


class MockDataManager:
    def getConfig(self, bot_hash):
        return dict(data_mod.configDefault)

    def getMemory(self, bot_hash):
        return mock_memory


data_mod.gData = MockDataManager()

# 现在可以安全导入 agent
import OlivOSAIChatAssassin.agent as agent_mod

# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name, condition, detail=''):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f'  [PASS] {name}')
    else:
        FAIL_COUNT += 1
        print(f'  [FAIL] {name} {detail}')


def make_plugin_event():
    event = MagicMock()
    event.bot_info.hash = 'test_bot_hash'
    event.base_info = {'self_id': '999888777'}
    return event


def make_history():
    return [
        {'message': '大家好', 'nickname': '小明', 'user_id': '12345', 'time': '10:00'},
        {'message': '今天天气不错', 'nickname': '小红', 'user_id': '67890', 'time': '10:01'},
        {'message': '有人想跑团吗', 'nickname': '小明', 'user_id': '12345', 'time': '10:02'},
    ]


# ===========================================================================
print('\n=== 1. _truncate 截断 ===')
# ===========================================================================
check('短文本不截断', agent_mod._truncate('hello', 100) == 'hello')
long_text = 'x' * 3000
truncated = agent_mod._truncate(long_text, 2000)
check('长文本截断到2000+后缀', len(truncated) == 2000 + len('\n...[结果已截断]'))
check('截断后缀正确', truncated.endswith('\n...[结果已截断]'))

# ===========================================================================
print('\n=== 2. _get_enabled_tools 名字映射 ===')
# ===========================================================================
config_default = dict(data_mod.configDefault)
tools_default = agent_mod._get_enabled_tools(config_default)
check('默认配置返回全部8个工具', len(tools_default) == 8, f'got {len(tools_default)}')

config_partial = dict(config_default)
config_partial['agent_tools'] = ['knowledge', 'web_search']
tools_partial = agent_mod._get_enabled_tools(config_partial)
check('部分配置只返回2个工具', len(tools_partial) == 2, f'got {len(tools_partial)}')
names_partial = [t['function']['name'] for t in tools_partial]
check('短名knowledge映射到search_knowledge', 'search_knowledge' in names_partial)
check('短名web_search映射到web_search', 'web_search' in names_partial)

config_fullname = dict(config_default)
config_fullname['agent_tools'] = ['search_knowledge', 'list_images']
tools_fullname = agent_mod._get_enabled_tools(config_fullname)
check('直接写全名也兼容', len(tools_fullname) == 2)

config_invalid = dict(config_default)
config_invalid['agent_tools'] = 'not_a_list'
tools_invalid = agent_mod._get_enabled_tools(config_invalid)
check('非list配置返回全部工具', len(tools_invalid) == 8)

# ===========================================================================
print('\n=== 3. _build_extra_body thinking 透传 ===')
# ===========================================================================
config_think_off = dict(config_default)
config_think_off['thinking'] = {'type': 'disabled'}
extra_off = agent_mod._build_extra_body(config_think_off)
check('thinking disabled', extra_off['thinking'] == {'type': 'disabled'})
check('disabled时无reasoning_effort', 'reasoning_effort' not in extra_off)

config_think_on = dict(config_default)
config_think_on['thinking'] = {'type': 'enabled'}
config_think_on['reasoning_effort'] = 'high'
extra_on = agent_mod._build_extra_body(config_think_on)
check('thinking enabled', extra_on['thinking'] == {'type': 'enabled'})
check('enabled时带reasoning_effort', extra_on.get('reasoning_effort') == 'high')

config_no_think = dict(config_default)
config_no_think.pop('thinking', None)
extra_none = agent_mod._build_extra_body(config_no_think)
check('无thinking配置默认disabled', extra_none['thinking'] == {'type': 'disabled'})

# ===========================================================================
print('\n=== 4. _build_system_prompt ===')
# ===========================================================================
ctx = agent_mod.AgentContext(
    bot_hash='test_bot_hash',
    group_id='group_001',
    plugin_event=make_plugin_event(),
    config=config_default,
    history=make_history(),
)
prompt = agent_mod._build_system_prompt(ctx)
check('包含人格设定', '伪装成人类的自豪的新锐AI' in prompt)
check('包含QQ号', '999888777' in prompt)
check('包含@格式说明', '[OP:at,id=999888777]' in prompt)
check('包含工具使用指引', '调用工具' in prompt)
check('包含JSON输出要求', '{"r":' in prompt)

# ===========================================================================
print('\n=== 5. execute_tool 分发 ===')
# ===========================================================================
result_unknown = agent_mod.execute_tool('nonexistent_tool', {}, ctx)
check('未知工具返回错误', '未知工具' in result_unknown)

result_knowledge = agent_mod.execute_tool('search_knowledge', {'query': '天气'}, ctx)
check('知识检索有结果', '晴天' in result_knowledge, f'got: {result_knowledge[:100]}')

result_knowledge_empty = agent_mod.execute_tool('search_knowledge', {'query': ''}, ctx)
check('空query返回错误', '不能为空' in result_knowledge_empty)

result_profile = agent_mod.execute_tool('get_user_profile', {'user_id': '12345'}, ctx)
check('用户侧写正确', '小明' in result_profile)

result_profile_miss = agent_mod.execute_tool('get_user_profile', {'user_id': '99999'}, ctx)
check('未知用户返回未找到', '未找到' in result_profile_miss)

result_memory = agent_mod.execute_tool('get_group_memory', {}, ctx)
check('群记忆正确', 'TRPG' in result_memory)

result_images = agent_mod.execute_tool('list_images', {}, ctx)
check('图片列表包含cat.png', 'cat.png' in result_images)
check('图片列表包含描述', '一只猫' in result_images)

result_group_info = agent_mod.execute_tool('get_group_info', {}, ctx)
check('群信息包含群号', 'group_001' in result_group_info)
check('群信息包含bot QQ号', '999888777' in result_group_info)

result_skills = agent_mod.execute_tool('search_skills', {'query': 'TRPG规则'}, ctx)
check('技能检索调用成功', '技能片段' in result_skills or '未找到' in result_skills)

# ===========================================================================
print('\n=== 6. _tool_web_search 未启用时的行为 ===')
# ===========================================================================
result_web = agent_mod.execute_tool('web_search', {'query': 'test'}, ctx)
check('web_search未启用返回提示', '未启用' in result_web)

# ===========================================================================
print('\n=== 7. _tool_web_fetch 协议校验 ===')
# ===========================================================================
result_fetch_bad = agent_mod.execute_tool('web_fetch', {'url': 'ftp://evil.com'}, ctx)
check('非http协议被拒绝', '仅支持' in result_fetch_bad)

result_fetch_empty = agent_mod.execute_tool('web_fetch', {'url': ''}, ctx)
check('空url返回错误', '不能为空' in result_fetch_empty)

# ===========================================================================
print('\n=== 8. agent_reply fallback（OpenAI=None） ===')
# ===========================================================================
original_openai = agent_mod.OpenAI
agent_mod.OpenAI = None
result_fallback = agent_mod.agent_reply(
    make_plugin_event(), 'group_001', make_history(), config_default
)
check('OpenAI=None时返回None', result_fallback is None)
agent_mod.OpenAI = original_openai

# ===========================================================================
print('\n=== 9. _get_client 缓存 ===')
# ===========================================================================
if original_openai is not None:
    agent_mod._client_cache.clear()
    c1 = agent_mod._get_client(config_default)
    c2 = agent_mod._get_client(config_default)
    check('相同配置返回同一client', c1 is c2)
    config_other = dict(config_default)
    config_other['api_base'] = 'https://other.api.com/v1'
    c3 = agent_mod._get_client(config_other)
    check('不同api_base返回不同client', c3 is not c1)
    agent_mod._client_cache.clear()
else:
    print('  [SKIP] openai 未安装，跳过 client 缓存测试')

# ===========================================================================
print('\n=== 10. agent_reply 完整循环（mock OpenAI client） ===')
# ===========================================================================


class MockToolCall:
    def __init__(self, name, arguments):
        self.id = f'call_{name}_{int(time.time()*1000)}'
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = json.dumps(arguments, ensure_ascii=False)


class MockMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, **kwargs):
        d = {'role': 'assistant', 'content': self.content}
        if self.tool_calls:
            d['tool_calls'] = [
                {'id': tc.id, 'function': {'name': tc.function.name, 'arguments': tc.function.arguments}}
                for tc in self.tool_calls
            ]
        if kwargs.get('exclude_none'):
            d = {k: v for k, v in d.items() if v is not None}
        return d


class MockChoice:
    def __init__(self, message):
        self.message = message


class MockUsage:
    total_tokens = 100
    prompt_tokens = 80
    completion_tokens = 20


class MockResponse:
    def __init__(self, message):
        self.choices = [MockChoice(message)]
        self.usage = MockUsage()


class MockCompletions:
    def __init__(self):
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            # 第一轮：模型调用工具
            return MockResponse(MockMessage(
                content=None,
                tool_calls=[MockToolCall('search_knowledge', {'query': 'TRPG'})]
            ))
        else:
            # 第二轮：模型给出最终回复
            return MockResponse(MockMessage(
                content='{"r":["来跑团吧！我知道一些TRPG规则"]}',
                tool_calls=None
            ))


class MockChat:
    def __init__(self):
        self.completions = MockCompletions()


class MockClient:
    def __init__(self, **kwargs):
        self.chat = MockChat()


# 注入 mock client（需要 OpenAI 非 None 才能进入循环）
original_openai_for_loop = agent_mod.OpenAI
agent_mod.OpenAI = MockClient  # truthy 占位，实际不会调用构造函数（client 从缓存取）
agent_mod._client_cache.clear()
mock_client = MockClient()
cache_key = (config_default.get('api_key', '') or 'sk-not-configured', config_default.get('api_base', ''))
agent_mod._client_cache[cache_key] = mock_client

result_agent = agent_mod.agent_reply(
    make_plugin_event(), 'group_001', make_history(), config_default
)
check('agent循环返回list', isinstance(result_agent, list), f'got {type(result_agent)}')
check('回复内容正确', result_agent == ['来跑团吧！我知道一些TRPG规则'], f'got {result_agent}')
check('API被调用2次（1轮工具+1轮最终）', mock_client.chat.completions.call_count == 2,
      f'got {mock_client.chat.completions.call_count}')

# ===========================================================================
print('\n=== 11. agent_reply max_turns 强制收尾 ===')
# ===========================================================================


class MockCompletionsAlwaysTool:
    def __init__(self):
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        # 检查是否传了 tools（最终收尾轮不传 tools）
        if kwargs.get('tools') is None:
            return MockResponse(MockMessage(content='{"r":["算了不说了"]}', tool_calls=None))
        # 工具轮永远调工具
        return MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall('get_group_memory', {})]
        ))


class MockChatAlwaysTool:
    def __init__(self):
        self.completions = MockCompletionsAlwaysTool()


class MockClientAlwaysTool:
    def __init__(self, **kwargs):
        self.chat = MockChatAlwaysTool()


agent_mod._client_cache.clear()
mock_client2 = MockClientAlwaysTool()
agent_mod._client_cache[cache_key] = mock_client2

config_maxturns = dict(config_default)
config_maxturns['agent_max_turns'] = 3
result_maxturns = agent_mod.agent_reply(
    make_plugin_event(), 'group_001', make_history(), config_maxturns
)
check('max_turns后仍能返回回复', result_maxturns == ['算了不说了'], f'got {result_maxturns}')
# 3轮工具 + 1轮强制收尾 = 4次调用
check('调用次数=3+1', mock_client2.chat.completions.call_count == 4,
      f'got {mock_client2.chat.completions.call_count}')

# ===========================================================================
print('\n=== 12. 工具轮不传 response_format ===')
# ===========================================================================


class MockCompletionsCheckFormat:
    def __init__(self):
        self.format_in_tool_turn = None
        self.format_in_final_turn = None
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            self.format_in_tool_turn = kwargs.get('response_format')
            return MockResponse(MockMessage(
                content=None,
                tool_calls=[MockToolCall('get_group_info', {})]
            ))
        else:
            self.format_in_final_turn = kwargs.get('response_format')
            return MockResponse(MockMessage(content='{"r":["ok"]}', tool_calls=None))


class MockChatCheckFormat:
    def __init__(self):
        self.completions = MockCompletionsCheckFormat()


class MockClientCheckFormat:
    def __init__(self, **kwargs):
        self.chat = MockChatCheckFormat()


agent_mod._client_cache.clear()
mock_client3 = MockClientCheckFormat()
agent_mod._client_cache[cache_key] = mock_client3

agent_mod.agent_reply(make_plugin_event(), 'group_001', make_history(), config_default)
check('工具轮无response_format', mock_client3.chat.completions.format_in_tool_turn is None,
      f'got {mock_client3.chat.completions.format_in_tool_turn}')

# ===========================================================================
print('\n=== 13. model_dump exclude_none 验证 ===')
# ===========================================================================
msg_with_none = MockMessage(content=None, tool_calls=[MockToolCall('list_images', {})])
dumped = msg_with_none.model_dump(exclude_none=True)
check('exclude_none移除content=None', 'content' not in dumped or dumped.get('content') is not None)
check('tool_calls保留', 'tool_calls' in dumped)

# 恢复 OpenAI 引用
agent_mod.OpenAI = original_openai_for_loop

# ===========================================================================
# 汇总
# ===========================================================================
print(f'\n{"="*60}')
print(f'回归测试完成：{PASS_COUNT} PASS / {FAIL_COUNT} FAIL / 共 {PASS_COUNT + FAIL_COUNT} 项')
if FAIL_COUNT > 0:
    print('!!! 存在失败项，请检查 !!!')
    sys.exit(1)
else:
    print('全部通过。')
    sys.exit(0)
