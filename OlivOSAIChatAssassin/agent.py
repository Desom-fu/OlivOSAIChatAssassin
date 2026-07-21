"""Agent harness: OpenAI tool-calling 循环，按需检索信息后产出群聊回复。"""

import json
import re
import time
import socket
import ipaddress
import dataclasses
import threading
from typing import Any
from urllib.parse import urlparse

import requests

import OlivOSAIChatAssassin

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AgentContext:
    bot_hash: str
    group_id: str
    plugin_event: Any
    config: dict
    history: list


# ---------------------------------------------------------------------------
# OpenAI client 缓存（按 api_key + api_base 复用连接池）
# ---------------------------------------------------------------------------

_client_cache: 'dict[tuple[str, str], Any]' = {}
_client_cache_lock = threading.Lock()


def _get_client(config: dict):
    api_key = config.get('api_key', '') or 'sk-not-configured'
    api_base = config.get('api_base', 'https://api.deepseek.com/v1')
    cache_key = (api_key, api_base)
    with _client_cache_lock:
        if cache_key in _client_cache:
            return _client_cache[cache_key]
    timeout = 60
    try:
        timeout = float(config.get('timeout', 60))
    except (TypeError, ValueError):
        pass
    client = OpenAI(
        api_key=api_key,
        base_url=api_base,
        timeout=timeout,
        max_retries=2,
    )
    with _client_cache_lock:
        _client_cache[cache_key] = client
    return client


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

AGENT_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "从群聊积累的知识库中检索与查询相关的知识条目",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_skills",
            "description": "从技能库中检索与当前话题相关的规则书/知识片段",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "技能检索关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": "获取指定用户的心理侧写信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID"}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_group_memory",
            "description": "获取当前群的聊天总结/前情提要",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索获取实时信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取指定URL的网页文本内容（截断至4000字符）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标URL（http/https）"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_images",
            "description": "列出当前群图片缓存中可用的图片文件名及描述",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_group_info",
            "description": "获取当前群的基本信息（群号、本bot的QQ号等）",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]

# 配置短名 → 工具全名 映射
_TOOL_NAME_MAP = {
    'knowledge': 'search_knowledge',
    'skills': 'search_skills',
    'profile': 'get_user_profile',
    'memory': 'get_group_memory',
    'web_search': 'web_search',
    'web_fetch': 'web_fetch',
    'images': 'list_images',
    'group_info': 'get_group_info',
}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_TOOL_RESULT_MAX_CHARS = 4000


def _truncate(text: str, limit: int = _TOOL_RESULT_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + '\n...[结果已截断]'


def _tool_search_knowledge(args: dict, ctx: AgentContext) -> str:
    query = str(args.get('query', '')).strip()
    if not query:
        return '错误：query 不能为空'
    results = {}
    for key_gMemory in ('知识缓存', '知识库', '知识搜索'):
        if key_gMemory == '知识库':
            dict_map = OlivOSAIChatAssassin.data.gStaticKnowledge
            rate = 0.15
        else:
            dict_map = (
                OlivOSAIChatAssassin.data.gData
                .getMemory(ctx.bot_hash)
                .get('全局', {})
                .get(key_gMemory, {})
            )
            rate = 0.1
        if not isinstance(dict_map, dict) or len(dict_map) == 0:
            continue
        matched = OlivOSAIChatAssassin.tools.peak_up_recommendMatch(
            target=query,
            dictMap=dict_map,
            dictName=f'agent_{key_gMemory}',
            ageing=ctx.config.get(
                'search_ageing',
                OlivOSAIChatAssassin.data.configDefault['search_ageing']
            ),
            rate=rate,
            matchedList=list(results.keys())
        )
        results.update(matched)
    if not results:
        return '未找到相关知识'
    return _truncate(json.dumps(results, ensure_ascii=False))


def _tool_search_skills(args: dict, ctx: AgentContext) -> str:
    query = str(args.get('query', '')).strip()
    if not query:
        return '错误：query 不能为空'
    fake_history = [{'message': query, 'nickname': '查询', 'user_id': '0', 'time': ''}]
    try:
        context_str = OlivOSAIChatAssassin.skillManager.get_skills_context(
            fake_history, ctx.bot_hash
        )
    except Exception as e:
        return f'技能检索失败：{e}'
    if not context_str:
        return '未找到相关技能片段'
    return _truncate(context_str)


def _tool_get_user_profile(args: dict, ctx: AgentContext) -> str:
    user_id = str(args.get('user_id', '')).strip()
    if not user_id:
        return '错误：user_id 不能为空'
    profiles = (
        OlivOSAIChatAssassin.data.gData
        .getMemory(ctx.bot_hash)
        .get('全局', {})
        .get('用户侧写', {})
    )
    if user_id in profiles:
        return f'{user_id}: {profiles[user_id]}'
    return f'未找到用户 {user_id} 的侧写信息'


def _tool_get_group_memory(args: dict, ctx: AgentContext) -> str:
    memory = (
        OlivOSAIChatAssassin.data.gData
        .getMemory(ctx.bot_hash)
        .get(ctx.group_id, OlivOSAIChatAssassin.data.gMemoryDefaultStr)
    )
    return str(memory)


def _tool_web_search(args: dict, ctx: AgentContext) -> str:
    query = str(args.get('query', '')).strip()
    if not query:
        return '错误：query 不能为空'
    web_config = ctx.config.get(
        'web_search', OlivOSAIChatAssassin.data.configDefault.get('web_search', {})
    )
    if not web_config.get('enable', False):
        return '联网搜索未启用（需在配置中开启 web_search.enable）'
    max_results = int(web_config.get('max_results', 3))
    backend = web_config.get('backend', 'duckduckgo')
    if backend == 'duckduckgo':
        if DDGS is None:
            return '错误：duckduckgo_search 库未安装'
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return '搜索无结果'
            lines = []
            for i, r in enumerate(results, 1):
                title = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                lines.append(f'{i}. {title}\n   {body}\n   链接: {href}')
            return _truncate('\n'.join(lines))
        except Exception as e:
            return f'搜索失败：{e}'
    elif backend == 'bing':
        try:
            resp = requests.get(
                'https://cn.bing.com/search',
                params={'q': query, 'count': max_results},
                headers={
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/125.0.0.0 Safari/537.36'
                    ),
                    'Accept-Language': 'zh-CN,zh;q=0.9',
                },
                timeout=10
            )
            if resp.status_code != 200:
                return f'Bing 搜索失败：HTTP {resp.status_code}'
            html = resp.text
            # 解析 <li class="b_algo"> 结果块
            blocks = re.findall(
                r'<li class="b_algo">(.*?)</li>', html, flags=re.S
            )
            if not blocks:
                return '搜索无结果'
            lines = []
            for i, block in enumerate(blocks[:max_results], 1):
                title_m = re.search(r'<h2[^>]*>.*?<a[^>]*>(.*?)</a>', block, flags=re.S)
                href_m = re.search(r'<a[^>]+href="(https?://[^"]+)"', block)
                snippet_m = re.search(
                    r'<p[^>]*>(.*?)</p>', block, flags=re.S
                )
                title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else ''
                href = href_m.group(1) if href_m else ''
                snippet = re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip() if snippet_m else ''
                lines.append(f'{i}. {title}\n   {snippet}\n   链接: {href}')
            return _truncate('\n'.join(lines)) if lines else '搜索无结果'
        except Exception as e:
            return f'Bing 搜索失败：{e}'
    elif backend == 'api':
        api_base = web_config.get('api_base', '').rstrip('/')
        api_key = web_config.get('api_key', '')
        if not api_base:
            return '错误：web_search.api_base 未配置'
        try:
            resp = requests.get(
                f'{api_base}/search',
                params={'q': query, 'count': max_results},
                headers={'Authorization': f'Bearer {api_key}'} if api_key else {},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else data.get('results', [])
                lines = []
                for i, r in enumerate(items[:max_results], 1):
                    title = r.get('title', '')
                    snippet = r.get('snippet', r.get('body', ''))
                    url = r.get('url', r.get('link', ''))
                    lines.append(f'{i}. {title}\n   {snippet}\n   链接: {url}')
                return _truncate('\n'.join(lines)) if lines else '搜索无结果'
            return f'搜索API错误：HTTP {resp.status_code}'
        except Exception as e:
            return f'搜索失败：{e}'
    return f'未知搜索后端：{backend}'


# ---------------------------------------------------------------------------
# SSRF 防护
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]


def _is_safe_url(url: str) -> 'tuple[bool, str]':
    """校验 URL 是否安全（非内网/环回/元数据地址）。返回 (是否安全, 原因)。"""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, '无法解析主机名'
        # 解析 DNS 获取真实 IP（防止 DNS rebinding 需在连接层再验证，此处做首层过滤）
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 80, proto=socket.IPPROTO_TCP)
        for family, _, _, _, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return False, f'目标地址 {ip_str} 属于内网/环回/链路本地地址'
            for network in _BLOCKED_NETWORKS:
                if ip in network:
                    return False, f'目标地址 {ip_str} 属于受限网段 {network}'
        return True, ''
    except socket.gaierror:
        return False, 'DNS 解析失败'
    except Exception as e:
        return False, f'URL 校验异常：{e}'


def _tool_web_fetch(args: dict, ctx: AgentContext) -> str:
    url = str(args.get('url', '')).strip()
    if not url:
        return '错误：url 不能为空'
    if not url.startswith(('http://', 'https://')):
        return '错误：仅支持 http/https 协议'
    # SSRF 防护：校验目标地址
    safe, reason = _is_safe_url(url)
    if not safe:
        OlivOSAIChatAssassin.logger.warn(f'AGENT SSRF BLOCKED: {url} - {reason}')
        return f'拒绝访问：{reason}'
    try:
        resp = requests.get(url, timeout=10, allow_redirects=False, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; OlivOSAgent/1.0)'
        })
        if resp.status_code in (301, 302, 303, 307, 308):
            return '抓取失败：目标发生重定向（已禁止跟随重定向）'
        if resp.status_code != 200:
            return f'抓取失败：HTTP {resp.status_code}'
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.S | re.I)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S | re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
        else:
            text = resp.text
        if len(text) > 4000:
            text = text[:4000] + '\n...[内容已截断]'
        return text if text else '页面内容为空'
    except Exception as e:
        return f'抓取失败：{e}'


def _tool_list_images(args: dict, ctx: AgentContext) -> str:
    cache_queue = OlivOSAIChatAssassin.data.gImageCache.get(ctx.group_id, [])
    if not cache_queue:
        return '当前群没有缓存的图片'
    lines = []
    for item in cache_queue:
        if isinstance(item, tuple) and len(item) >= 2:
            file_name, image_data = item[0], item[1]
            desc = ''
            if isinstance(image_data, dict):
                desc = image_data.get('content', '')
                img_type = image_data.get('type', '')
                if img_type:
                    desc = f'[{img_type}] {desc}'
            lines.append(f'- {file_name}: {desc}')
    return _truncate('\n'.join(lines)) if lines else '当前群没有缓存的图片'


def _tool_get_group_info(args: dict, ctx: AgentContext) -> str:
    self_id = ''
    try:
        self_id = str(ctx.plugin_event.base_info.get('self_id', ''))
    except Exception:
        pass
    info = {
        '群号': ctx.group_id,
        'bot_QQ号': self_id,
        '当前时间': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    return json.dumps(info, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

_TOOL_DISPATCH = {
    'search_knowledge': _tool_search_knowledge,
    'search_skills': _tool_search_skills,
    'get_user_profile': _tool_get_user_profile,
    'get_group_memory': _tool_get_group_memory,
    'web_search': _tool_web_search,
    'web_fetch': _tool_web_fetch,
    'list_images': _tool_list_images,
    'get_group_info': _tool_get_group_info,
}


def execute_tool(name: str, arguments: dict, ctx: AgentContext) -> str:
    handler = _TOOL_DISPATCH.get(name)
    if handler is None:
        return f'未知工具：{name}'
    try:
        return handler(arguments, ctx)
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'AGENT TOOL ERR [{name}]: {e}')
        return f'工具执行出错：{e}'


# ---------------------------------------------------------------------------
# Agent system prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(ctx: AgentContext) -> str:
    personality = ctx.config.get('personality', '')
    self_id = ''
    try:
        self_id = str(ctx.plugin_event.base_info.get('self_id', ''))
    except Exception:
        pass
    mention_str = f'[OP:at,id={self_id}]'
    return f'''# 规则
- 你是一个人类，这是你日常水群的群聊，你会根据你的人格设定自行决定要不要参与群聊对话
- "[OP:at,id=【QQ号】]"表示你被@，这是群聊中@的格式，你也可以使用这种格式
- 越新的消息越重要，不要重复去回复已经回复过的消息，除非有必要
- 谨记你是在进行聊天，所以不要把括号之类的内容发出来，不需要你描述自己的动作或者心理活动，这只会让人起疑
- 群聊历史中最后一条群聊消息总是最新；历史之后的当前动态上下文不是群聊消息
- 你可以使用工具获取信息：查知识库、搜索技能、联网搜索、查看图片缓存等
- 只在需要额外信息时调用工具，简单闲聊不需要调用任何工具
- 当用户明确要求搜索、查找、查询某信息时，必须先调用工具尝试检索，拿到结果后再决定是否追问；不要在不搜索的情况下直接反问用户
- 【最高优先级】你的最终输出必须是且仅是一个JSON对象，禁止输出任何自然语言、解释、思考过程。要回复时输出 {{"r":["回复内容"]}}，不回复时输出 {{"r":[]}}。不允许在JSON前后附加任何文字
- 如要发送附加信息中的图片文件，在列表中以单条消息发送的格式发送，格式 [发图片:文件名]

# 人格设定
- {personality}

# 已知信息
- 你的QQ号是：{self_id}，所以你被@时是：{mention_str}
'''


# ---------------------------------------------------------------------------
# Fallback: 非标准 JSON 输出的意图判断
# ---------------------------------------------------------------------------

_SKIP_KEYWORDS = (
    '不回复', '不回', '跳过', '不参与', '不感兴趣', '不需要回复',
    '沉默', '旁观', '不插话', '不说话', '不发言', '无需回复',
    '与我无关', '不相关', '不需要我', '没有需要',
)


def _fallback_parse_intent(content: str) -> list:
    """当 LLM 输出非标准 JSON 时，判断是"不回复"还是实际回复内容。
    检测文本首尾 15 字符是否以跳过关键词开头/结尾，中间不管，避免误判。"""
    head = content[:15]
    tail = content[-15:] if len(content) > 15 else content
    for kw in _SKIP_KEYWORDS:
        if head.startswith(kw) or tail.endswith(kw):
            OlivOSAIChatAssassin.logger.warn(f'AGENT - FALLBACK SKIP (keyword: {kw})')
            return []
    # 未命中跳过关键词，视为实际回复
    OlivOSAIChatAssassin.logger.warn('AGENT - FALLBACK RAW CONTENT as reply')
    return [content]


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def _get_enabled_tools(config: dict) -> list[dict]:
    """根据配置短名列表过滤可用工具。"""
    enabled_short = config.get(
        'agent_tools',
        OlivOSAIChatAssassin.data.configDefault.get('agent_tools', [])
    )
    if not isinstance(enabled_short, list):
        return list(AGENT_TOOL_DEFINITIONS)
    # 短名 → 全名
    enabled_full = set()
    for name in enabled_short:
        if name in _TOOL_NAME_MAP:
            enabled_full.add(_TOOL_NAME_MAP[name])
        else:
            # 兼容直接写全名的情况
            enabled_full.add(name)
    return [
        tool for tool in AGENT_TOOL_DEFINITIONS
        if tool.get('function', {}).get('name', '') in enabled_full
    ]


def _build_extra_body(config: dict) -> dict:
    """从配置中提取 thinking/reasoning_effort 等供应商扩展参数。"""
    extra = {}
    thinking = config.get('thinking', {'type': 'disabled'})
    if isinstance(thinking, dict) and isinstance(thinking.get('type'), str):
        extra['thinking'] = thinking
    else:
        extra['thinking'] = {'type': 'disabled'}
    if extra['thinking'].get('type') == 'enabled':
        reasoning_effort = config.get('reasoning_effort', 'max')
        extra['reasoning_effort'] = reasoning_effort
    return extra


def agent_reply(
    plugin_event,
    group_id: str,
    history: list,
    config: dict
) -> 'list|None':
    """Agent tool-calling 主循环。返回 reply_list 或 None（None 触发旧路径 fallback）。"""
    if OpenAI is None:
        OlivOSAIChatAssassin.logger.warn('AGENT: openai 库未安装，回退到单轮模式')
        return None

    bot_hash = str(plugin_event.bot_info.hash)
    ctx = AgentContext(
        bot_hash=bot_hash,
        group_id=group_id,
        plugin_event=plugin_event,
        config=config,
        history=history,
    )

    # 构建 messages
    system_prompt = _build_system_prompt(ctx)
    messages = OlivOSAIChatAssassin.msg.get_ai_context(
        config, history, system_prompt,
        patch={
            '当前上下文': {
                '群号': group_id,
                '当前本地时间': time.strftime('%Y-%m-%d %H:%M:%S'),
                '说明': '群聊历史中最后一条群聊消息是最新消息；本动态上下文不是群聊消息。',
            },
        },
        handler_list=[OlivOSAIChatAssassin.msg.img_handler]
    )
    messages.append({
        'role': 'user',
        'content': (
            '根据上一条群聊消息决定是否回复。如果你需要额外信息来回复，可以调用工具获取。'
            '最终只输出严格JSON对象：要回复时输出 {"r":["回复内容"]}；不回复时输出 {"r":[]}。'
            '不要输出解释或其它文本。'
        )
    })

    # 初始化参数
    client = _get_client(config)
    model = config.get('model', 'deepseek-v4-flash')
    max_tokens = config.get('max_tokens', 2048)
    temperature = config.get('temperature', 0.7)
    try:
        max_turns = int(config.get(
            'agent_max_turns',
            OlivOSAIChatAssassin.data.configDefault.get('agent_max_turns', 5)
        ))
    except (TypeError, ValueError):
        max_turns = 5
    if max_turns < 1:
        max_turns = 5
    tools = _get_enabled_tools(config)
    extra_body = _build_extra_body(config)

    OlivOSAIChatAssassin.logger.log(
        f'AGENT - START [{model}, tools={len(tools)}, max_turns={max_turns}]'
    )
    start_time = time.perf_counter()

    for turn in range(max_turns):
        OlivOSAIChatAssassin.logger.log(f'AGENT - TURN [{turn + 1}/{max_turns}]')
        try:
            # 工具轮：传 tools，不传 response_format（模型需自由输出 tool_calls）
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice='auto' if tools else None,
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body=extra_body,
            )
        except Exception as e:
            OlivOSAIChatAssassin.logger.warn(f'AGENT API ERR: {e}')
            return None

        choice = response.choices[0] if response.choices else None
        if choice is None:
            OlivOSAIChatAssassin.logger.warn('AGENT: 无 choices 返回')
            return None

        assistant_message = choice.message

        # 记录 usage
        if response.usage:
            OlivOSAIChatAssassin.logger.log(
                f'AGENT USAGE - TOKEN - {response.usage.total_tokens} '
                f'({response.usage.prompt_tokens}/{response.usage.completion_tokens})'
            )

        # 无 tool_calls → 最终回复
        if not assistant_message.tool_calls:
            content = (assistant_message.content or '').strip()
            OlivOSAIChatAssassin.logger.log(f'AGENT - FINAL: {content[:200]}')
            reply_list = OlivOSAIChatAssassin.msg.get_json_message(content)
            if reply_list is None and content:
                # LLM 输出了非标准 JSON，判断是否为"不回复"意图
                reply_list = _fallback_parse_intent(content)
            elapsed = time.perf_counter() - start_time
            OlivOSAIChatAssassin.logger.log(f'AGENT - DONE {elapsed:.2f} s, turns={turn + 1}')
            return reply_list

        # 有 tool_calls → 执行并继续循环
        messages.append(assistant_message.model_dump(exclude_none=True))
        for tool_call in assistant_message.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments or '{}')
            except json.JSONDecodeError:
                fn_args = {}
            OlivOSAIChatAssassin.logger.log(f'AGENT TOOL CALL - {fn_name}({fn_args})')
            tool_result = execute_tool(fn_name, fn_args, ctx)
            OlivOSAIChatAssassin.logger.log(
                f'AGENT TOOL RESULT - {fn_name}: {tool_result[:200]}'
            )
            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'content': tool_result,
            })

    # 达到最大轮次，强制收尾（不传 tools，传 response_format 约束 JSON）
    OlivOSAIChatAssassin.logger.warn(f'AGENT - MAX TURNS REACHED ({max_turns})')
    messages.append({
        'role': 'user',
        'content': '你已经调用了足够多的工具。现在请直接输出最终JSON回复：{"r":["回复内容"]} 或 {"r":[]}。'
    })
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        if response.choices:
            content = (response.choices[0].message.content or '').strip()
            reply_list = OlivOSAIChatAssassin.msg.get_json_message(content)
            if reply_list is None and content:
                reply_list = _fallback_parse_intent(content)
            return reply_list
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'AGENT FINAL FORCED ERR: {e}')
    return None
