import json
import random
import time
import threading
import re
import os
import hashlib
from difflib import SequenceMatcher
from datetime import datetime
from collections import deque
from typing import Optional, Callable, Tuple

import OlivOS
import OlivOSAIChatAssassin


FORWARD_PATTERN = re.compile(r'\[OP:forward,id=([^\]]+)\]')
MFACE_PATTERN = re.compile(r'\[(?:CQ|OP):mface,[^\]]*(?:\[[^\]]*\])*[^\]]*\]')
OP_IMAGE_PATTERN = re.compile(r'\[OP:image,[^\]]+\]')
IMAGE_CODE_PATTERN = re.compile(r'\[图片：[^\]]*\]')


def extract_tag_param(tag: str, key: str) -> 'str|None':
    if not isinstance(tag, str):
        return None
    match = re.search(rf'{re.escape(key)}=([^,\]]+)', tag)
    if match:
        return match.group(1)
    return None


def guess_file_ext_from_url(url: 'str|None', default_ext: str = '.jpg') -> str:
    if not isinstance(url, str) or len(url) <= 0:
        return default_ext
    clean_url = url.split('?', 1)[0]
    _, ext = os.path.splitext(clean_url)
    ext = ext.lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
        return ext
    return default_ext


def get_mface_file_name(tag: str) -> str:
    file_name = extract_tag_param(tag, 'file')
    if isinstance(file_name, str) and len(file_name) > 0:
        return file_name
    url = extract_tag_param(tag, 'url')
    summary = extract_tag_param(tag, 'summary') or ''
    digest = hashlib.md5(f'{url or tag}|{summary}'.encode('utf-8')).hexdigest().upper()
    return f'{digest}{guess_file_ext_from_url(url, default_ext=".png")}'


def build_basic_image_cache_data(message_text: str, image_type: str = '图片', summary: 'str|None' = None) -> dict:
    if isinstance(message_text, str):
        if '表情包' in message_text or '梗图' in message_text:
            image_type = '表情包'
        elif '照片' in message_text:
            image_type = '照片'
    content = '用户刚发送的一张图片'
    if isinstance(summary, str) and len(summary.strip()) > 0:
        content = summary.strip()[:32]
    return {
        'content': content,
        'intent': '用户可能希望保存、查看或再次发送这张图片',
        'type': image_type
    }


def format_timestamp(timestamp) -> 'str|None':
    if not isinstance(timestamp, (int, float)):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp).astimezone().replace(microsecond=0).isoformat()
    except Exception:
        return None


def flatten_message_content(content) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join([flatten_message_content(item) for item in content])
    if isinstance(content, dict):
        segment_type = content.get('type')
        data = content.get('data')
        if segment_type == 'text':
            if isinstance(data, dict) and isinstance(data.get('text'), str):
                return data.get('text', '')
            if isinstance(content.get('text'), str):
                return content.get('text', '')
        if segment_type == 'image':
            return '[图片]'
        if segment_type in ('mface', 'face'):
            return '[表情]'
        if segment_type == 'reply':
            if isinstance(data, dict) and data.get('id') is not None:
                return f'[回复:{data.get("id")}]'
            return '[回复]'
        if segment_type == 'at':
            if isinstance(data, dict) and data.get('qq') is not None:
                return f'[OP:at,id={data.get("qq")}]'
            return '[艾特]'
        if segment_type == 'record':
            return '[语音]'
        if segment_type == 'video':
            return '[视频]'
        if segment_type == 'file':
            return '[文件]'
        if segment_type == 'forward':
            forward_id = None
            if isinstance(data, dict):
                forward_id = data.get('id') or data.get('message_id')
            if forward_id is None:
                forward_id = content.get('id') or content.get('message_id')
            if forward_id is not None:
                return f'[OP:forward,id={forward_id}]'
            return '[合并转发]'
        for key in ('message', 'content', 'text', 'raw_message'):
            value = content.get(key)
            if value is not None:
                return flatten_message_content(value)
        if isinstance(data, dict):
            for key in ('text', 'message', 'content'):
                value = data.get(key)
                if value is not None:
                    return flatten_message_content(value)
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def format_forward_node(plugin_event: OlivOS.API.Event, node: dict, depth: int = 0) -> str:
    if not isinstance(node, dict):
        return str(node)
    sender = node.get('sender', {})
    sender_name = '未知用户'
    sender_id = None
    if isinstance(sender, dict):
        sender_name = sender.get('name') or sender.get('nickname') or sender_name
        sender_id = sender.get('id') or sender.get('user_id')
    sender_name = node.get('nickname') or node.get('name') or sender_name
    sender_id = node.get('user_id') or node.get('sender_id') or sender_id
    time_text = format_timestamp(node.get('time')) or format_timestamp(node.get('sender_time')) or '未知时间'
    content = None
    for key in ('message', 'content', 'raw_message', 'text'):
        if key in node:
            content = node.get(key)
            break
    message = flatten_message_content(content)
    if message:
        message = expand_forward_messages(plugin_event, message, depth=depth + 1)
        message = clean_media_tags(message)
    else:
        message = '[空消息]'
    sender_text = f'[{sender_name}]'
    if sender_id is not None:
        sender_text = f'[{sender_name}]({sender_id})'
    return f'{time_text} {sender_text} 说: "{message}"'


def render_forward_message(plugin_event: OlivOS.API.Event, forward_message_id, depth: int = 0) -> str:
    if depth > 4:
        return f'[合并转发:{forward_message_id}]'
    try:
        res = plugin_event.get_forward_msg(forward_message_id)
        if not isinstance(res, dict):
            return f'[合并转发:{forward_message_id}]'
        if not res.get('active'):
            return f'[合并转发:{forward_message_id}]'
        data = res.get('data', {})
        if not isinstance(data, dict):
            return f'[合并转发:{forward_message_id}]'
        messages = data.get('messages', [])
        if not isinstance(messages, list) or len(messages) <= 0:
            return f'[合并转发:{forward_message_id} 空]'
        lines = [format_forward_node(plugin_event, node, depth=depth) for node in messages]
        return '[合并转发开始]\n' + '\n'.join(lines) + '\n[合并转发结束]'
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'FORWARD FATAL: {forward_message_id} - {e}')
        return f'[合并转发:{forward_message_id}]'


def expand_forward_messages(plugin_event: OlivOS.API.Event, message: str, depth: int = 0) -> str:
    if not isinstance(message, str):
        return flatten_message_content(message)
    if plugin_event is None or '[OP:forward,' not in message:
        return message
    if depth > 4:
        return FORWARD_PATTERN.sub('[合并转发]', message)

    def _forward_replace(match):
        return render_forward_message(plugin_event, match.group(1), depth=depth)

    return FORWARD_PATTERN.sub(_forward_replace, message)


def clean_media_tags(message: str) -> str:
    if not isinstance(message, str):
        return message
    res = OP_IMAGE_PATTERN.sub('[图片]', message)

    def _mface_clean(match):
        summary = extract_tag_param(match.group(0), 'summary')
        if isinstance(summary, str) and len(summary) > 0:
            return summary
        return '[表情]'

    res = MFACE_PATTERN.sub(_mface_clean, res)
    res = re.sub(r'\[OP:record.+?\]', '[语音]', res)
    res = re.sub(r'\[OP:video.+?\]', '[视频]', res)
    res = re.sub(r'\[OP:json.+?"prompt":"(.+?)".*?\]', r'[卡片：\1]', res)
    res = re.sub(r'\[OP:json.+?\]', '[卡片]', res)
    return res


def strip_media_for_search(message: str) -> str:
    if not isinstance(message, str):
        return ''
    res = IMAGE_CODE_PATTERN.sub(' ', message)
    res = OP_IMAGE_PATTERN.sub(' ', res)
    res = MFACE_PATTERN.sub(' ', res)
    res = re.sub(r'\[图片\]', ' ', res)
    res = re.sub(r'\[表情\]', ' ', res)
    res = re.sub(r'\[语音[^\]]*\]', ' ', res)
    res = re.sub(r'\[视频[^\]]*\]', ' ', res)
    res = re.sub(r'\[卡片[^\]]*\]', ' ', res)
    res = re.sub(r'\s+', ' ', res)
    return res.strip()


def extract_image_file_names(message: str) -> list[str]:
    if not isinstance(message, str):
        return []
    file_names = []
    for match in OP_IMAGE_PATTERN.finditer(message):
        params = OlivOSAIChatAssassin.tools.opcode_parse_params('image', match.group(0))
        file_name = params.get('file')
        if isinstance(file_name, str) and file_name and file_name not in file_names:
            file_names.append(file_name)
    for match in MFACE_PATTERN.finditer(message):
        file_name = get_mface_file_name(match.group(0))
        if file_name not in file_names:
            file_names.append(file_name)
    return file_names


def unity_group_message(plugin_event: OlivOS.API.Event, Proc):
    # 群消息事件入口
    group_id = str(plugin_event.data.group_id)
    bot_hash = plugin_event.bot_info.hash
    OlivOSAIChatAssassin.data.gGroupLock.setdefault(
        group_id,
        OlivOSAIChatAssassin.tools.SlackableFairLock(
            slack_time=OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
                'slack_time',
                OlivOSAIChatAssassin.data.configDefault['slack_time']
            ),
            cooldown_time=OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
                'slack_cooldown_time',
                OlivOSAIChatAssassin.data.configDefault['slack_cooldown_time']
            )
        )
    )
    with OlivOSAIChatAssassin.data.gGroupLock[group_id]:
        OlivOSAIChatAssassin.msg.unity_group_message_router(plugin_event, Proc)


def unity_group_message_router(plugin_event: OlivOS.API.Event, Proc):
    group_id = str(plugin_event.data.group_id)
    bot_hash = plugin_event.bot_info.hash

    # 仅在文件变化时重新加载配置和记忆（避免高频磁盘 I/O）
    OlivOSAIChatAssassin.data.gData.reload(bot_hash)
    OlivOSAIChatAssassin.load.load_history()
    if not OlivOSAIChatAssassin.data.gData.getConfig(bot_hash):
        return
    # 检查是否在启用群组列表中
    if (
        'enabled_groups' in OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
        and (
            group_id not in OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)['enabled_groups']
            and 'all' not in OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)['enabled_groups']
        )
    ):
        return
    # 忽略前缀消息
    message = plugin_event.data.message
    message = msg_trans(message, group_id, plugin_event=plugin_event, bot_hash=bot_hash)
    message = msg_wash(message)
    if should_ignore(message, bot_hash=bot_hash):
        OlivOSAIChatAssassin.logger.log('IGNORE')
        return
    # 添加消息到历史
    if group_id not in OlivOSAIChatAssassin.data.gMessageHistory:
        history_keep, history_max_grow = OlivOSAIChatAssassin.load.get_history_limits(
            OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
        )
        OlivOSAIChatAssassin.data.gMessageHistory[group_id] = OlivOSAIChatAssassin.tools.DynamicQueue(
            keep=history_keep,
            max_grow=history_max_grow,
        )
    message_id = plugin_event.data.message_id
    if -1 == message_id:
        message_id = None
    add_message_to_history(
        group_id, message, plugin_event.data.user_id, plugin_event.data.sender.get('nickname', '用户'),
        message_id=message_id,
        bot_hash=bot_hash
    )
    # 决定是否回复
    if not should_reply(group_id, message, plugin_event, bot_hash=bot_hash):
        OlivOSAIChatAssassin.logger.log('SHOULD NOT')
    else:
        reply_to_group(plugin_event, group_id, message)


def should_ignore(message, bot_hash: str):
    if not OlivOSAIChatAssassin.data.gData.getConfig(bot_hash):
        return False
    if len(message) <= 0:
        return True
    ignore_prefixes = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('ignore_prefixes', [])
    for prefix in ignore_prefixes:
        if message.startswith(prefix):
            return True
    return False


def add_message_to_history(
    group_id, message, user_id, nickname,
    message_id: 'str|None' = None,
    *,
    bot_hash: Optional[str] = None
):
    if group_id not in OlivOSAIChatAssassin.data.gMessageHistory:
        return
    config = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
    image_expire = config.get(
        'image_expire_time',
        OlivOSAIChatAssassin.data.configDefault['image_expire_time']
    )
    now = time.time()
    for msg_history in OlivOSAIChatAssassin.data.gMessageHistory[group_id]:
        if isinstance(msg_history, dict):
            msg_history['message'] = OlivOSAIChatAssassin.webTools.sanitize_media_message(
                msg_history.get('message', ''),
                timestamp=msg_history.get('timestamp', 0),
                image_expire=image_expire,
                now=now
            )
    timestamp = now
    message_new = OlivOSAIChatAssassin.webTools.sanitize_media_message(
        message,
        timestamp=timestamp,
        image_expire=image_expire,
        now=now
    )
    max_len = config.get(
        'max_message_length',
        OlivOSAIChatAssassin.data.configDefault['max_message_length']
    )
    if len(message_new) > max_len and '[OP:image,' not in message_new and ':mface,' not in message_new and '[合并转发开始]' not in message_new:
        message_new = message_new[:max_len] + '...'
    msg_entry = {
        'timestamp': timestamp,
        'time': datetime.now().astimezone().replace(microsecond=0).isoformat(),
        'user_id': user_id,
        'nickname': nickname,
        'message': message_new
    }
    if message_id is not None:
        msg_entry['message_id'] = str(message_id)
    OlivOSAIChatAssassin.data.gMessageHistory[group_id].append(msg_entry)
    OlivOSAIChatAssassin.load.write_history(group_id)


def should_reply(group_id, message, plugin_event, bot_hash: str):
    if not OlivOSAIChatAssassin.data.gData.getConfig(bot_hash):
        return False
    # 检查是否被@
    self_id = plugin_event.base_info['self_id']
    mention_str = f'[OP:at,id={self_id}]'
    if OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('mention_reply', True) and mention_str in message:
        return True
    # 检查是否包含关键词
    keywords = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('reply_keywords', [])
    for kw in keywords:
        if kw in message:
            return True
    # 随机概率回复
    prob = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
        'reply_probability', OlivOSAIChatAssassin.data.configDefault["reply_probability"]
    )
    if random.random() < prob:
        return True
    return False


def reply_to_group(plugin_event: OlivOS.API.Event, group_id: str, message: str):
    bot_hash: str = str(plugin_event.bot_info.hash)
    total_start = time.perf_counter()
    if (
        not OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
        or not OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('api_key')
    ):
        return
    # 构建对话历史
    history: 'list[dict]' = list(OlivOSAIChatAssassin.data.gMessageHistory.get(group_id, []))
    if not history:
        return
    elif len(history) <= OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
        'history_size_min', OlivOSAIChatAssassin.data.configDefault['history_size_min']
    ):
        OlivOSAIChatAssassin.logger.log('HISTORY TOO SHORT')
        return
    self_id = plugin_event.base_info['self_id']
    mention_str = f'[OP:at,id={self_id}]'
    personality = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('personality', '')
    record_knowledge = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('record_knowledge', True)
    contentDefault = f'''
# 规则
- 你是一个人类，这是你日常水群的群聊，你会根据你的人格设定自行决定要不要参与群聊对话
- "[OP:at,id=【QQ号】]"表示你被@，这是群聊中@的格式，你也可以使用这种格式
- 越新的消息越重要，不要重复去回复已经回复过的消息，除非有必要
- 谨记你是在进行聊天，所以不要把括号之类的内容发出来，不需要你描述自己的动作或者心理活动，这只会让人起疑
- 群聊历史中最后一条群聊消息总是最新；历史之后的当前动态上下文不是群聊消息

# 人格设定
- {personality}

# 已知信息
- 你的QQ号是：{self_id}，所以你被@时是：{mention_str}
'''

    # 生成记忆
    def set_memory(t_thisMemory: dict):
        history = list(OlivOSAIChatAssassin.data.gMessageHistory.get(group_id, []))
        # 设置任务
        examples_knowledge = {
            "k": {
                "中国": "五千年文明古国，幅员辽阔，正全面推进民族复兴，坚持和平发展。"
            },
            "u": {
                "123456789": "小明：阳光开朗，积极乐观，乐于社交，推测为男孩"
            },
            "g": "我们刚刚聊到了中国"
        }
        content = '''
# 信息
前情提要附加在最末尾

# 当前任务
'''
        if record_knowledge:
            content += '''
## 分析知识点，将结果输出至k键的值中
- 分析当前聊天记录，提炼需要记住的知识点，注意不是对于现状的记录，记录常识性的知识
- 优先记录卡片中的信息，因为转发的平台卡片通常更可信
- 每条知识点长度限制在32字以内
- 每条知识带有一个介于2至8字之间的关键词，被用于作为子字符串进行搜索
- 知识点以Json对象的格式输出，知识点的关键词为键，内容为值
'''
        content += '''
## 对遇到的每个用户进行心理侧写，将结果输出至u键的值中
- 分析当前聊天记录，对遇到的每个用户进行心理侧写
- 根据语言习惯推断性别
- 每条心理侧写描述的键使用用户的 user_id
- 格式参考以下参考输出，一定要附带名称
- 每条心理侧写描述长度限制在32字以内
'''
        content += '''
## 总结本群聊天记录，将结果作为字符串输出至g的值中
- 对聊天记录进行总结
- 杜绝流水账，请每次都决定自己需要记住什么东西
- 最终长度限制在128字以内
'''
        content += f'''
# 参考输出
{json.dumps(examples_knowledge, ensure_ascii=False)}
'''
        # 格式化历史为OpenAI消息格式
        summary = (
            OlivOSAIChatAssassin.data.gData
            .getMemory(bot_hash)
            .get(group_id, OlivOSAIChatAssassin.data.gMemoryDefaultStr)
        )
        messages = get_ai_context(
            OlivOSAIChatAssassin.data.gData.getConfig(bot_hash),
            history,
            content,
            patch={'前情提要': summary},
        )
        messages.append({
            'role': 'user',
            'content': '现在提炼以上对话中的重要知识点，只输出符合要求的JSON对象。',
        })
        # 调用 API
        try:
            call_ai_res = OlivOSAIChatAssassin.webTools.call_ai(
                OlivOSAIChatAssassin.data.gData.getConfig(bot_hash), messages,
                temperature_override=0.7,
                flag_thinking_override=False,
                reasoning_effort_override="max",
                response_format_override={"type": "json_object"}
            )
            call_ai_data = json.loads(call_ai_res)
            knowledge_data: dict = {}
            user_data: dict = {}
            group_memory_data: 'str|None' = None
            with OlivOSAIChatAssassin.data.gMemoryLock:
                if '全局' not in OlivOSAIChatAssassin.data.gData.getMemory(bot_hash):
                    OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)['全局'] = {}
                if (
                    'k' in call_ai_data
                    and type(call_ai_data['k']) is dict
                ):
                    knowledge_data = call_ai_data['k']
                valid_knowledge_data = {}
                for k, v in knowledge_data.items():
                    if (
                        type(k) is str
                        and type(v) is str
                    ):
                        valid_knowledge_data[k] = v
                        OlivOSAIChatAssassin.logger.log(f'[更新知识] - {k}\n{v}')
                removed_knowledge = OlivOSAIChatAssassin.load.update_knowledge_cache(
                    OlivOSAIChatAssassin.data.gData.getMemory(bot_hash),
                    valid_knowledge_data,
                    OlivOSAIChatAssassin.data.gData.getConfig(bot_hash),
                )
                if removed_knowledge:
                    OlivOSAIChatAssassin.logger.log(
                        f'[知识缓存淘汰] - {len(removed_knowledge)} 条: '
                        f'{", ".join(removed_knowledge)}'
                    )
                if (
                    'u' in call_ai_data
                    and type(call_ai_data['u']) is dict
                ):
                    user_data = call_ai_data['u']
                if '用户侧写' not in OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)['全局']:
                    OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)['全局']['用户侧写'] = {}
                for k, v in user_data.items():
                    if (
                        type(k) is str
                        and type(v) is str
                    ):
                        OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)['全局']['用户侧写'][k] = v
                        OlivOSAIChatAssassin.logger.log(f'[更新侧写] - {k}\n{v}')
                if (
                    'g' in call_ai_data
                    and type(call_ai_data['g']) is str
                ):
                    group_memory_data = call_ai_data['g']
                OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)[group_id] = group_memory_data
                OlivOSAIChatAssassin.logger.log(
                    f'[本群记忆]\n{OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)[group_id]}'
                )
            OlivOSAIChatAssassin.load.write_memory(bot_hash=bot_hash)
        except Exception as e:
            OlivOSAIChatAssassin.logger.warn(f'API FATAL: {e}')

    # 设置任务
    thisMemoryC = {}
    thisMemoryG = {}
    with OlivOSAIChatAssassin.data.gMemoryLock:
        for k, v in OlivOSAIChatAssassin.data.gData.getMemory(bot_hash).get('全局', {}).items():
            if k not in (
                '人物关系',
                '知识缓存',
                '知识搜索',
                '用户侧写',
                '图片缓存',
            ):
                thisMemoryC[k] = v
    key_gMemory_const = '知识搜索'
    key_staticKnowledge = '知识库'
    thisMemoryG[key_gMemory_const] = {}
    thisMemoryG_patch = {}
    for key_gMemory in (
        '知识缓存',
        '知识库',
        '知识搜索',
    ):
        start = time.perf_counter()
        thisMemoryM = (
            OlivOSAIChatAssassin.data.gData
            .getMemory(bot_hash)
            .get('全局', {key_gMemory: {}})
            .get(key_gMemory, {})
        )
        rate_this = 0.1
        thisMemoryG_patch: dict[str, str] = {}
        if key_gMemory == key_staticKnowledge:
            thisMemoryM = OlivOSAIChatAssassin.data.gStaticKnowledge
            rate_this = 0.15
        for j in history:
            target_msg = strip_media_for_search(j.get('message', ''))
            if len(target_msg) <= 0:
                continue
            target_str = target_msg
            if j.get('nickname', '') is None:
                target_str = f"我()：{target_msg}"
            else:
                target_str = f"{j.get('nickname', '')}({j.get('user_id', '')})：{target_msg}"
            thisMemoryG_patch.update(
                OlivOSAIChatAssassin.tools.peak_up_recommendMatch(
                    target=target_str,
                    dictMap=thisMemoryM,
                    dictName=key_gMemory,
                    ageing=OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
                        'search_ageing',
                        OlivOSAIChatAssassin.data.configDefault['search_ageing']
                    ),
                    rate=rate_this,
                    matchedList=list(thisMemoryG_patch.keys())
                )
            )
        for _ in range(
            OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
                'search_knowledge_deepin',
                OlivOSAIChatAssassin.data.configDefault['search_knowledge_deepin'],
            )
        ):
            thisMemoryG_patch_deepin = {}
            for mem_this_data in thisMemoryG_patch:
                thisMemoryG_patch_deepin.update(
                    OlivOSAIChatAssassin.tools.peak_up_recommendMatch(
                        target=thisMemoryG_patch[mem_this_data],
                        dictMap=thisMemoryM,
                        dictName=key_gMemory,
                        ageing=OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
                            'search_ageing',
                            OlivOSAIChatAssassin.data.configDefault['search_ageing']
                        ),
                        rate=rate_this,
                        matchedList=list(thisMemoryG_patch.keys()) + list(thisMemoryG_patch_deepin.keys()),
                        father=mem_this_data
                    )
                )
            thisMemoryG_patch.update(thisMemoryG_patch_deepin)
        thisMemoryG[key_gMemory_const].update(thisMemoryG_patch)
        end = time.perf_counter()
        OlivOSAIChatAssassin.logger.log(f"CALL PEAK UP - [{key_gMemory}] - DONE {(end - start):.2f} s")
    key_gMemory_const = '人物关系'
    thisMemoryG[key_gMemory_const] = {}
    for key_gMemory in (
        '人物关系',
        '用户侧写'
    ):
        thisMemoryP = (
            OlivOSAIChatAssassin.data.gData
            .getMemory(bot_hash)
            .get('全局', {key_gMemory: {}})
            .get(key_gMemory, {})
        )
        if type(thisMemoryP) is dict:
            for k, v in thisMemoryP.items():
                flagHit = False
                flagHit_str = None
                for j in history:
                    message_clean = strip_media_for_search(j.get('message', '')).lower()
                    if k == j.get('user_id', None):
                        flagHit = True
                        flagHit_str = k
                        break
                    if (
                        type(v) is list
                        and len(v) >= 1
                    ):
                        if type(v[0]) is str:
                            if v[0] in message_clean:
                                flagHit = True
                                flagHit_str = v[0]
                                break
                        elif type(v[0]) is list:
                            for n in v[0]:
                                if n in message_clean:
                                    flagHit = True
                                    flagHit_str = n
                                    break
                if flagHit:
                    OlivOSAIChatAssassin.logger.log(f'PEAK UP - [{key_gMemory}] {flagHit_str}')
                    thisMemoryG[key_gMemory_const][k] = v
    thisGroupMemory = (
        OlivOSAIChatAssassin.data.gData
        .getMemory(bot_hash)
        .get(group_id, OlivOSAIChatAssassin.data.gMemoryDefaultStr)
    )
    thisGroupMemoryDict = {
        group_id: thisGroupMemory
    }
    thisMemory = {
        '全局': thisMemoryG
    }
    thisMemory.update(thisGroupMemoryDict)
    if not OlivOSAIChatAssassin.data.gGroupLock[group_id].slack():
        OlivOSAIChatAssassin.logger.log(
            f'NEXT - {time.perf_counter() - total_start:.2f}'
            f'/{OlivOSAIChatAssassin.data.gGroupLock[group_id].getRemaining():.2f} s - {message}'
        )
        return
    else:
        OlivOSAIChatAssassin.logger.log(
            f'HIT - {time.perf_counter() - total_start:.2f}'
            f'/{OlivOSAIChatAssassin.data.gGroupLock[group_id].getRemaining():.2f} s - {message}'
        )
    # --- Skills 动态注入 ---
    skills_context = ''
    if OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
        'skills_enable', OlivOSAIChatAssassin.data.configDefault['skills_enable']
    ):
        skills_context = OlivOSAIChatAssassin.skillManager.get_skills_context(
            history, bot_hash
        )
    examples_reply = {
        'r': ['好的']
    }
    content = f'''{contentDefault}
# 信息
- 历史和最新消息中都附带可用的富文本资源，可以借用，但不要编造不存在的资源
- 群聊历史之后附带当前动态上下文，其中包含当前记忆、图片缓存、群号和本地时间
- 越新的消息越重要

# 当前任务
## 将回复内容输出至r的值中
- 即便思考也要保证Json格式输出的完整，任何时候都要保证Json格式输出的完整
- 当你不想参与对话时，你会令r的值的列表为空，这是你必须遵守的规则，你不需要每句话都回复，你需要按照你的心情来，但是当有人找你时尽量回复
- 判断是否应该加入聊天进行回复
- 根据自己已经回复过的消息，避免重复已经回应过的话题，避免重复自己说过的话
- 如果应该回复，将回复内容追加至r的值的列表中，多条消息需要分开
- 如要发送附加信息中的图片文件，在列表中以单条消息发送的格式发送，格式 [发图片:文件名]

# 参考输出，以严格的Json格式输出
{json.dumps(examples_reply, ensure_ascii=False)}

# 固定记忆
- {json.dumps(thisMemoryC, ensure_ascii=False)}
'''
    content_first_think = f'''{contentDefault}
# 信息
- 群聊历史之后附带当前动态上下文，其中包含当前记忆、图片缓存、群号和本地时间
- 越新的消息越重要

# 固定记忆
- {json.dumps(thisMemoryC, ensure_ascii=False)}

# 当前任务
## 你是一个二分类器，只判断最新一条群消息是否应该交给正式回复模型
- 你的任务不是写回复，不是续写，不是抽取资料，不是解释原因
- 只能输出一个JSON对象：{{"d":"NEXT","i":"图片内容或意图关键词或空字符串"}} 或 {{"d":"SKIP","i":""}}
- d = NEXT 表示应该交给正式回复模型
- d = SKIP 表示不应该交给正式回复模型
- i 表示如果这次适合发图片，则填写你想要的图片内容或意图关键词；如果不需要发图片则为空字符串
- i 应该填写短描述，例如“白色小狗被递麦克风采访”“熊猫头捂脸哭泣”“无奈表情包”
- 不要填写或编造图片文件名，不要输出 .jpg、.png、.gif 等扩展名
- 即使用户没有明确要求发图，如果当前语境很适合用表情包回应，也可以主动从当前图片缓存的内容或意图中选择关键词填入 i
- 主动发图要保守，只有图片明显贴合最新消息的情绪、吐槽、调侃、无奈、哭笑不得、卖萌或求回应时才选择图片

## 必须输出 NEXT 的情况
- 最新消息 @ 你、回复你、称呼你的名字/昵称、问候你、向你提问、要求你做事
- 最新消息明显是在邀请你接话，或者你不确定是否在找你
- 例如“下午好啊”“在吗”“你怎么看”“帮我看看”都输出 {{"d":"NEXT"}}
- 例如用户要求发图、发表情包、随便发个表情包时，输出 {{"d":"NEXT","i":"从当前图片缓存里选择的图片内容或意图关键词"}}
- 例如最新消息是在吐槽、撒娇、崩溃、无奈、调侃你或需要情绪回应，且当前图片缓存里有明显贴切的表情包，可以输出 {{"d":"NEXT","i":"贴切图片的内容或意图关键词"}}

## 可以输出 SKIP 的情况
- 最新消息只是其他群友之间的闲聊，且没有指向你
- 最新消息只是表情、语气词、无明确对象的短句，且你没有合适接话点
- 最新消息是正经任务、规则问题、资料解释、需要文字帮助的请求时，除非用户明确要求发图，否则 i 应为空字符串
- 如果没有非常贴切的图片，i 必须为空字符串，不要为了发图硬选

## 输出要求
- 不要输出历史内容
- 不要输出知识库内容
- 不要输出正式回复
- 不要输出解释
- 只能输出 {{"d":"NEXT","i":"图片内容或意图关键词或空字符串"}} 或 {{"d":"SKIP","i":""}}
'''
    # 格式化历史为OpenAI消息格式
    _, history_size_max_print = OlivOSAIChatAssassin.load.get_history_limits(
        OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
    )
    OlivOSAIChatAssassin.logger.log(f"HISTORY - SIZE [{len(history)}/{history_size_max_print}]")
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    messages_patch = {
        '当前上下文': {
            '群号': group_id,
            '当前本地时间': now_text,
            '说明': '群聊历史中最后一条群聊消息是最新消息；本动态上下文不是群聊消息。',
        },
        '当前记忆': thisMemory,
        '图片缓存': dict(OlivOSAIChatAssassin.data.gImageCache.get(group_id, {})),
    }
    if skills_context:
        messages_patch['技能片段'] = skills_context.strip()
    intent_image_cache = get_intent_image_cache(bot_hash, group_id)
    messages = get_ai_context(
        OlivOSAIChatAssassin.data.gData.getConfig(bot_hash), history, content,
        patch=messages_patch,
        handler_list=[img_handler]
    )
    messages.append(
        {
            "role": "user",
            "content": '根据上一条群聊消息决定是否回复，只输出严格JSON对象。要回复时输出 {"r":["回复内容"]}；不回复时输出 {"r":[]}。不要输出解释或其它文本。'
        }
    )
    messages_first_think_patch = {
        '当前上下文': {
            '群号': group_id,
            '当前本地时间': now_text,
            '说明': '群聊历史中最后一条群聊消息是最新消息；本动态上下文不是群聊消息。',
        },
        '当前记忆': thisMemory,
        '图片缓存': intent_image_cache
    }
    messages_first_think = get_ai_context(
        OlivOSAIChatAssassin.data.gData.getConfig(bot_hash), history, content_first_think,
        patch=messages_first_think_patch,
        handler_list=[img_handler]
    )
    messages_first_think.append(
        {
            "role": "user",
            "content": '根据上一条群消息完成二分类，只输出 {"d":"NEXT","i":"图片内容或意图关键词或空字符串"} 或 {"d":"SKIP","i":""}。如果上一条消息在称呼、问候、询问或要求你，输出 {"d":"NEXT","i":""}。如果用户要求发图或发表情包，从当前图片缓存中选择一个贴切图片的内容或意图关键词填入 i，不要填写或编造文件名，不要输出扩展名。即使用户没要求发图，若最新消息很适合用表情包回应，也可以保守选择一个贴切图片关键词；没有明显贴切图片就让 i 为空。'
        }
    )
    # 调用 API
    reply_list = None
    reply_count = 0
    retry_count = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
        "retry_count", OlivOSAIChatAssassin.data.configDefault["retry_count"]
    )
    first_thinking_done = False
    first_thinking_pass = True
    first_thinking_image_ref = ''
    first_thinking_image_added = False
    try:
        while (
            reply_list is None
            and reply_count < retry_count
        ):
            reply_count += 1
            OlivOSAIChatAssassin.logger.log(f"CALL AI - TRY [{reply_count}/{retry_count}]")
            first_thinking = (
                OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
                .get('first_thinking', OlivOSAIChatAssassin.data.configDefault['first_thinking'])
            )
            flag_need_think = True
            if (
                first_thinking_done is True
            ):
                flag_need_think = first_thinking_pass
            elif (
                first_thinking is True
                and OlivOSAIChatAssassin.tools.get_think(bot_hash, group_id)
            ):
                first_thinking_done = True
                flag_need_think = False
                intent_config = OlivOSAIChatAssassin.webTools.get_intent_ai_config(
                    OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
                )
                first_thinking_res = OlivOSAIChatAssassin.webTools.call_ai(
                    intent_config, messages_first_think,
                    flag_thinking_override=False,
                    response_format_override={"type": "json_object"}
                )
                OlivOSAIChatAssassin.logger.log(f'FIRST THINK RES - {first_thinking_res}')
                first_thinking_str = ''
                try:
                    first_thinking_data = json.loads(first_thinking_res)
                    if type(first_thinking_data) is dict:
                        first_thinking_str = str(first_thinking_data.get('d', '')).upper()
                        first_thinking_image_ref = str(first_thinking_data.get('i', '')).strip()
                except Exception:
                    first_thinking_str = str(first_thinking_res).strip().upper()
                if first_thinking_str.startswith('NEXT'):
                    flag_need_think = True
                elif first_thinking_str.startswith('SKIP'):
                    flag_need_think = False
                    reply_list = []
                else:
                    OlivOSAIChatAssassin.logger.warn(f'FIRST THINK DATA ERR: {first_thinking_res}')
                    flag_need_think = True
                first_thinking_pass = flag_need_think
                if flag_need_think:
                    OlivOSAIChatAssassin.logger.log(f"FIRST THINK - PASS - {first_thinking_res}")
                else:
                    OlivOSAIChatAssassin.logger.log(f"FIRST THINK - SKIP - {first_thinking_res}")
                    reply_list = []
            if flag_need_think:
                if first_thinking_image_ref and first_thinking_image_added is False:
                    first_thinking_image_added = True
                    image_cache_map = get_image_cache_map(bot_hash)
                    image_file_name = resolve_image_ref(first_thinking_image_ref, image_cache_map)
                    if image_file_name is not None:
                        messages.append(
                            {
                                "role": "user",
                                "content": f'本次如果要发送图片，优先使用这个真实图片文件名：[发图片:{image_file_name}]。不要改写文件名，不要使用图片描述当文件名。'
                            }
                        )
                    else:
                        OlivOSAIChatAssassin.logger.warn(f'FIRST THINK IMAGE REF ERR: {first_thinking_image_ref}')
                reply_list = get_json_message(
                    OlivOSAIChatAssassin.webTools.call_ai(
                        OlivOSAIChatAssassin.data.gData.getConfig(bot_hash), messages,
                        response_format_override={"type": "json_object"}
                    )
                )
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'API FATAL: {e}')
    # 发送回复
    if reply_list is None:
        OlivOSAIChatAssassin.logger.log('NONE')
    else:
        if len(reply_list) <= 0:
            OlivOSAIChatAssassin.logger.log('SKIP')
        else:
            OlivOSAIChatAssassin.tools.set_think(bot_hash, group_id)
            reply_list = reply_wash(reply_list, bot_hash=bot_hash)
            OlivOSAIChatAssassin.logger.log(f'REPLY - {reply_list}')
            add_message_to_history(group_id, ''.join(reply_history_wash(reply_list)), None, None, bot_hash=bot_hash)
            t_set_memory = threading.Thread(
                target=set_memory,
                args=(thisMemory, )
            )
            t_set_memory.start()
            OlivOSAIChatAssassin.tools.sleep(1 + (random.random() * 2 - 1) * 0.95)
            reply(
                plugin_event, reply_trans(reply_list, bot_hash=bot_hash),
                total_time_past=time.perf_counter() - total_start
            )
            t_set_memory.join()


def get_ai_context(
    lConfig,
    history,
    content,
    flagMerge: bool = False,
    prefix: str = "总结如下记录：",
    patch: Optional[dict | str] = None,
    handler_list: Optional[list[Callable]] = None
):
    # 格式化历史为OpenAI消息格式
    messages = []
    # 添加系统提示
    messages.append(
        {
            "role": "system",
            "content": content
        }
    )
    # 添加最近的历史消息，限制数量
    max_history_this = len(history)
    image_expire = lConfig.get(
        'image_expire_time',
        OlivOSAIChatAssassin.data.configDefault['image_expire_time']
    ) if lConfig else OlivOSAIChatAssassin.data.configDefault['image_expire_time']
    now = time.time()
    if flagMerge:
        chat_content = '\n'.join([
            f'{entry["time"]} [{entry["nickname"]}]({entry["user_id"]}) 说: "{clean_media_tags(OlivOSAIChatAssassin.webTools.sanitize_media_message(entry["message"], timestamp=entry.get("timestamp", 0), image_expire=image_expire, now=now))}"'
            if entry['nickname'] is not None
            else f'{entry["time"]} [我]() 说: "{clean_media_tags(OlivOSAIChatAssassin.webTools.sanitize_media_message(entry["message"], timestamp=entry.get("timestamp", 0), image_expire=image_expire, now=now))}"'
            for entry in list(history)[-max_history_this:]
        ])
        messages.append(
            {
                "role": "user",
                "content": f"{prefix}\n{chat_content}\n\n{patch}"
            }
        )
    else:
        count = 0
        for entry in list(history)[-max_history_this:]:
            count += 1
            if entry['nickname'] is None:
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"{entry['message']}"
                    }
                )
            else:
                entry_this = {}
                entry_this.update(entry)
                entry_this['message'] = clean_media_tags(
                    OlivOSAIChatAssassin.webTools.sanitize_media_message(
                        entry_this.get('message', ''),
                        timestamp=entry_this.get('timestamp', 0),
                        image_expire=image_expire,
                        now=now
                    )
                )
                if (
                    type(handler_list) is list
                    and type(patch) is dict
                ):
                    for handler in handler_list:
                        entry_this, patch = handler(entry_this, patch)
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(entry_this, ensure_ascii=False)
                    }
                )
        if type(patch) is dict and len(patch) > 0:
            messages.append(
                {
                    "role": "user",
                    "content": f"当前动态上下文：{json.dumps(patch, ensure_ascii=False)}"
                }
            )
    return messages


def img_handler(entry_data: dict, patch_data: dict[str, dict]) -> Tuple[dict, dict]:
    entry_res = entry_data
    patch_res = patch_data
    if (
        type(entry_data) is dict
        and type(patch_data) is dict
        and '图片缓存' in patch_data
        and type(patch_data['图片缓存']) is dict
    ):
        image_file_names = set(extract_image_file_names(entry_data.get('message', '')))
        for i in list(patch_data.get('图片缓存', {}).keys()):
            if i in image_file_names or OlivOSAIChatAssassin.tools.imgcode_format(patch_data['图片缓存'][i]) in entry_data['message']:
                entry_res.setdefault('当前图片缓存', {})
                entry_res['当前图片缓存'][i] = patch_data['图片缓存'][i]
                patch_res['图片缓存'].pop(i)
    return entry_res, patch_res


def get_json_message(data_str: str):
    res_list = []
    try:
        data_dict = json.loads(data_str)
        if (
            type(data_dict) is dict
            and 'r' in data_dict
            and type(data_dict['r']) is list
        ):
            for i in data_dict['r']:
                res_list.append(i)
            OlivOSAIChatAssassin.logger.log('DATA TYPE - JSON')
        else:
            res_list = None
            OlivOSAIChatAssassin.logger.warn(f'DATA TYPE ERR: {data_str}')
    except Exception:
        res_list = None
        OlivOSAIChatAssassin.logger.warn(f'DATA ERR: {data_str}')
    return res_list


def send_message_force(bot_hash, send_type, target_id, message):
    Proc = OlivOSAIChatAssassin.data.gProc
    if (
        Proc is not None
        and bot_hash in Proc.Proc_data['bot_info_dict']
    ):
        pluginName = OlivOSAIChatAssassin.data.gPluginName
        plugin_event = OlivOS.API.Event(
            OlivOS.contentAPI.fake_sdk_event(
                bot_info=Proc.Proc_data['bot_info_dict'][bot_hash],
                fakename=pluginName
            ),
            Proc.log
        )
        plugin_event.send(send_type, target_id, message)


def reply(plugin_event, msg: list, total_time_past: float = 0.0):
    flag_first = True
    for i in msg:
        len_i = len(i)
        if len_i <= 0:
            OlivOSAIChatAssassin.logger.log('SKIP - REPLY NONE')
        else:
            sleep_time = sum([
                0.2 + (random.random() * 2 - 1) * 0.15
                for _ in range(len_i)
            ])
            if flag_first:
                flag_first = False
                if sleep_time > total_time_past:
                    sleep_time -= total_time_past
            if sleep_time > 30:
                sleep_time /= 2
            OlivOSAIChatAssassin.tools.sleep(sleep_time)
            plugin_event.reply(i)


def reply_wash(msg: list, bot_hash: str):
    res = []
    # 限制消息长度
    max_len = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
        'max_message_length',
        OlivOSAIChatAssassin.data.configDefault['max_message_length']
    )
    for i in msg:
        res_i = i
        if type(res_i) is str:
            res_i = res_i.replace('\r', '')
            res_i = res_i.strip('\n')
            res_i = res_i.rstrip('。')
            res_i = re.sub(r'\(.+\)', '', res_i)
            res_i = re.sub(r'（.+）', '', res_i)
            if len(res_i) > max_len:
                res_i = res_i[:max_len]
            res.append(res_i)
    return res


def reply_history_wash(msg: list):
    res = []
    for i in msg:
        res_i = i
        if type(res_i) is str:
            res_i = re.sub(r'\[发图片\]', '', res_i)
            res_i = re.sub(r'\[图片.*?\]', '', res_i)
            res_i = re.sub(r'\[发图片:(.+)\]', '[发图片]', res_i)
            res.append(res_i)
    return res


def is_emoji_image_data(image_data: dict) -> bool:
    if type(image_data) is not dict:
        return False
    type_text = str(image_data.get('type', ''))
    intent_text = str(image_data.get('intent', ''))
    content_text = str(image_data.get('content', ''))
    target = f'{type_text} {intent_text} {content_text}'
    return any(i in target for i in ('表情包', '梗图', 'mface', '表情', 'emoji'))


def get_intent_image_cache(bot_hash: str, group_id: str) -> dict[str, dict]:
    image_cache_map = get_image_cache_map(bot_hash)
    current_image_names = [
        str(item[0])
        for item in OlivOSAIChatAssassin.data.gImageCache.get(group_id, [])
        if isinstance(item, tuple) and len(item) >= 1
    ]
    max_size = int(
        OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
            'intent_image_cache_size',
            OlivOSAIChatAssassin.data.configDefault['intent_image_cache_size']
        )
    )
    if max_size <= 0:
        return {}
    current_candidates = []
    global_candidates = []
    for file_name, image_data in image_cache_map.items():
        if not is_emoji_image_data(image_data):
            continue
        if file_name in current_image_names:
            current_candidates.append(file_name)
        else:
            global_candidates.append(file_name)
    random.shuffle(current_candidates)
    random.shuffle(global_candidates)
    selected_names = current_candidates + global_candidates[:max_size]
    res: dict[str, dict] = {}
    for file_name in selected_names:
        image_data = image_cache_map.get(file_name)
        if type(image_data) is dict:
            res[file_name] = image_data
    return res


def get_image_cache_map(bot_hash: 'str|None' = None) -> dict[str, dict]:
    res: dict[str, dict] = {}
    if bot_hash is not None and OlivOSAIChatAssassin.data.gData is not None:
        global_image_cache = (
            OlivOSAIChatAssassin.data.gData
            .getMemory(bot_hash)
            .get('全局', {})
            .get('图片缓存', {})
        )
        if type(global_image_cache) is dict:
            for key, value in global_image_cache.items():
                if type(value) is dict:
                    res[str(key)] = value
    for cache_queue in OlivOSAIChatAssassin.data.gImageCache.values():
        for item in cache_queue:
            if isinstance(item, tuple) and len(item) >= 2 and type(item[1]) is dict:
                res[str(item[0])] = item[1]
    return res


def reply_trans(msg: list, bot_hash: 'str|None' = None):
    res = []
    image_cache_map = get_image_cache_map(bot_hash)
    for i in msg:
        res_i = i
        image_dir = os.path.abspath(OlivOSAIChatAssassin.data.gImageDir)
        if type(res_i) is str:
            res_i = re.sub(r'\[发图片\]', '', res_i)
            res_i = re.sub(r'\[图片.*?\]', '', res_i)
            res_i = re.sub(
                r'\[发图片:(.+)\]',
                lambda m: image_tag_trans(m, image_dir, image_cache_map),
                res_i
            )
            res.append(res_i)
    return res


def image_tag_trans(match: re.Match, image_dir: str, image_cache_map: dict[str, dict]) -> str:
    image_ref = match.group(1).strip()
    file_name = resolve_image_ref(image_ref, image_cache_map)
    if file_name is None:
        OlivOSAIChatAssassin.logger.warn(f'IMAGE SEND SKIP - FILE NOT IN CACHE: {image_ref}')
        return ''
    file_path = os.path.abspath(os.path.join(image_dir, file_name))
    image_dir_with_sep = os.path.abspath(image_dir) + os.sep
    if not file_path.startswith(image_dir_with_sep) or not os.path.exists(file_path):
        OlivOSAIChatAssassin.logger.warn(f'IMAGE SEND SKIP - FILE NOT FOUND: {file_name}')
        return ''
    return f'[OP:image,file=file:///{file_path}]'


def resolve_image_ref(image_ref: str, image_cache_map: dict[str, dict]) -> 'str|None':
    if image_ref in image_cache_map:
        return image_ref
    normalized_file_name = normalize_image_file_name_ref(image_ref, image_cache_map)
    if normalized_file_name is not None:
        return normalized_file_name
    normalized_ref = normalize_image_lookup_text(image_ref)
    if not normalized_ref:
        return None
    requested_ext = get_image_ref_ext(image_ref)
    candidates: list[tuple[int, str]] = []
    for file_name, image_data in image_cache_map.items():
        score = score_image_lookup(normalized_ref, image_ref, requested_ext, file_name, image_data)
        if score > 0:
            candidates.append((score, file_name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_score, best_file_name = candidates[0]
    if best_score < 100:
        return None
    if len(candidates) >= 2 and candidates[1][0] == best_score:
        OlivOSAIChatAssassin.logger.warn(
            f'IMAGE SEND AMBIGUOUS PICK FIRST: {image_ref} -> {best_file_name}, {candidates[1][1]}'
        )
    OlivOSAIChatAssassin.logger.log(f'IMAGE SEND MATCH - {image_ref} -> {best_file_name}')
    return best_file_name


def normalize_image_file_name_ref(image_ref: str, image_cache_map: dict[str, dict]) -> 'str|None':
    if type(image_ref) is not str:
        return None
    image_ref = image_ref.strip()
    fixed_ref = re.sub(r'(\.[a-z0-9]{2,5})\1$', r'\1', image_ref, flags=re.IGNORECASE)
    if fixed_ref in image_cache_map:
        OlivOSAIChatAssassin.logger.log(f'IMAGE SEND FIX NAME - {image_ref} -> {fixed_ref}')
        return fixed_ref
    ref_stem = os.path.splitext(fixed_ref)[0].lower()
    if not ref_stem:
        return None
    candidates = [
        file_name
        for file_name in image_cache_map
        if os.path.splitext(file_name)[0].lower() == ref_stem
    ]
    if len(candidates) == 1:
        OlivOSAIChatAssassin.logger.log(f'IMAGE SEND FIX NAME - {image_ref} -> {candidates[0]}')
        return candidates[0]
    if len(candidates) > 1:
        requested_ext = get_image_ref_ext(fixed_ref)
        for file_name in candidates:
            if os.path.splitext(file_name)[1].lower().lstrip('.') == requested_ext:
                OlivOSAIChatAssassin.logger.log(f'IMAGE SEND FIX NAME - {image_ref} -> {file_name}')
                return file_name
    return None


def score_image_lookup(normalized_ref: str, image_ref: str, requested_ext: str, file_name: str, image_data: dict) -> int:
    score = 0
    normalized_file_name = normalize_image_lookup_text(file_name)
    if normalized_ref == normalized_file_name:
        score = max(score, 300)
    elif normalized_ref in normalized_file_name or normalized_file_name in normalized_ref:
        score = max(score, 180)
    else:
        score = max(score, score_fuzzy_image_text(normalized_ref, normalized_file_name, 180))
    for key, value_score in (
        ('content', 160),
        ('intent', 120),
        ('type', 80),
    ):
        value = image_data.get(key, '')
        if type(value) is not str:
            continue
        normalized_value = normalize_image_lookup_text(value)
        if not normalized_value:
            continue
        if normalized_ref == normalized_value:
            score = max(score, value_score + 80)
        elif normalized_ref in normalized_value or normalized_value in normalized_ref:
            score = max(score, value_score)
        else:
            score = max(score, score_fuzzy_image_text(normalized_ref, normalized_value, value_score))
    file_stem = os.path.splitext(file_name)[0].lower()
    ref_stem = os.path.splitext(image_ref.strip())[0].lower()
    if ref_stem and file_stem == ref_stem:
        score += 90
    file_ext = os.path.splitext(file_name)[1].lower().lstrip('.')
    if requested_ext and file_ext == requested_ext:
        score += 60
    elif not requested_ext and file_ext == 'gif':
        score += 20
    elif file_ext == 'gif':
        score += 20
    return score


def score_fuzzy_image_text(normalized_ref: str, normalized_value: str, max_score: int) -> int:
    if not normalized_ref or not normalized_value:
        return 0
    min_len = min(len(normalized_ref), len(normalized_value))
    max_len = max(len(normalized_ref), len(normalized_value))
    if min_len < 2:
        return 0
    length_rate = min_len / max_len
    if length_rate < get_image_fuzzy_length_rate_limit(min_len):
        return 0
    if min_len == 2 and max_len <= 3:
        same_position_count = sum(
            1
            for ref_char, value_char in zip(normalized_ref, normalized_value)
            if ref_char == value_char
        )
        if same_position_count >= 1:
            return int(max_score * 0.75)
    ratio = SequenceMatcher(None, normalized_ref, normalized_value).ratio()
    long_text_score = score_long_fuzzy_image_text(normalized_ref, normalized_value, max_score)
    if ratio < get_image_fuzzy_ratio_threshold(min_len):
        return long_text_score
    return max(int(max_score * ratio), long_text_score)


def get_image_fuzzy_length_rate_limit(min_len: int) -> float:
    if min_len >= 16:
        return 0.25
    if min_len >= 10:
        return 0.32
    if min_len >= 6:
        return 0.40
    return 0.45


def get_image_fuzzy_ratio_threshold(min_len: int) -> float:
    if min_len >= 24:
        return 0.48
    if min_len >= 16:
        return 0.54
    if min_len >= 10:
        return 0.60
    if min_len >= 6:
        return 0.68
    return 0.74


def score_long_fuzzy_image_text(normalized_ref: str, normalized_value: str, max_score: int) -> int:
    min_len = min(len(normalized_ref), len(normalized_value))
    if min_len < 8:
        return 0
    ref_chunks = get_image_text_chunks(normalized_ref)
    value_chunks = get_image_text_chunks(normalized_value)
    if not ref_chunks or not value_chunks:
        return 0
    common_chunks = ref_chunks & value_chunks
    if len(common_chunks) < 3:
        return 0
    chunk_cover = len(common_chunks) / min(len(ref_chunks), len(value_chunks))
    ref_chunk_cover = len(common_chunks) / len(ref_chunks)
    ref_chars = set(normalized_ref)
    value_chars = set(normalized_value)
    char_cover = len(ref_chars & value_chars) / min(len(ref_chars), len(value_chars))
    if chunk_cover < 0.18 and char_cover < 0.50:
        return 0
    score_rate = 0.46 + chunk_cover * 0.35 + ref_chunk_cover * 0.12 + char_cover * 0.12
    if min_len >= 16:
        score_rate += 0.05
    if min_len >= 24:
        score_rate += 0.04
    return int(max_score * min(score_rate, 0.95))


def get_image_text_chunks(text: str) -> set[str]:
    return {
        text[index:index + 2]
        for index in range(0, max(len(text) - 1, 0))
    }


def get_image_ref_ext(data: str) -> str:
    if type(data) is not str:
        return ''
    ext = os.path.splitext(data.strip())[1].lower().lstrip('.')
    if re.fullmatch(r'[a-z0-9]{2,5}', ext):
        return ext
    return ''


def normalize_image_lookup_text(data: str) -> str:
    if type(data) is not str:
        return ''
    res = data.strip().lower()
    res = re.sub(r'\.[a-z0-9]{2,5}$', '', res)
    res = re.sub(r'表情包|图片|照片|文件|发图片|发送|来一张|一张|这个|那个', '', res)
    res = re.sub(r'[\s\[\]【】()（）:：,，.;。;；"“”\'‘’_-]+', '', res)
    return res


def get_image_cleanup_time(bot_hash: str) -> float:
    return float(
        OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
            'image_cleanup_non_emoji_time',
            OlivOSAIChatAssassin.data.configDefault['image_cleanup_non_emoji_time']
        )
    )


def cleanup_non_emoji_images(bot_hash: str) -> None:
    cleanup_time = get_image_cleanup_time(bot_hash)
    if cleanup_time <= 0:
        return
    cleanup_all_bot_image_memory()
    cleanup_orphan_image_files(cleanup_time)
    cleanup_runtime_image_cache()


def cleanup_all_bot_image_memory() -> None:
    if OlivOSAIChatAssassin.data.gData is None:
        return
    now = time.time()
    image_dir = os.path.abspath(OlivOSAIChatAssassin.data.gImageDir)
    image_dir_with_sep = image_dir + os.sep
    for bot_hash_this in list(OlivOSAIChatAssassin.data.gData.memory.keys()):
        cleanup_time = get_image_cleanup_time(bot_hash_this)
        if cleanup_time <= 0:
            continue
        global_image_cache = (
            OlivOSAIChatAssassin.data.gData
            .getMemory(bot_hash_this)
            .setdefault('全局', {})
            .setdefault('图片缓存', {})
        )
        if type(global_image_cache) is not dict:
            continue
        flag_changed = False
        for file_name, image_data in list(global_image_cache.items()):
            if is_emoji_image_data(image_data):
                continue
            file_path = os.path.abspath(os.path.join(image_dir, str(file_name)))
            if not file_path.startswith(image_dir_with_sep):
                continue
            if not os.path.exists(file_path):
                global_image_cache.pop(file_name, None)
                flag_changed = True
                continue
            try:
                if now - os.path.getmtime(file_path) > cleanup_time:
                    os.remove(file_path)
                    global_image_cache.pop(file_name, None)
                    flag_changed = True
                    OlivOSAIChatAssassin.logger.log(f'IMAGE CLEANUP - {file_name}')
            except Exception as e:
                OlivOSAIChatAssassin.logger.warn(f'IMAGE CLEANUP ERR - {file_name}: {e}')
        if flag_changed:
            OlivOSAIChatAssassin.load.write_memory(bot_hash=bot_hash_this)


def get_referenced_image_files() -> set[str]:
    res: set[str] = set()
    if OlivOSAIChatAssassin.data.gData is not None:
        for bot_hash_this in list(OlivOSAIChatAssassin.data.gData.memory.keys()):
            global_image_cache = (
                OlivOSAIChatAssassin.data.gData
                .getMemory(bot_hash_this)
                .get('全局', {})
                .get('图片缓存', {})
            )
            if type(global_image_cache) is dict:
                res.update(str(file_name) for file_name in global_image_cache.keys())
    for cache_queue in OlivOSAIChatAssassin.data.gImageCache.values():
        for item in cache_queue:
            if isinstance(item, tuple) and len(item) >= 1:
                res.add(str(item[0]))
    return res


def cleanup_orphan_image_files(cleanup_time: float) -> None:
    image_dir = os.path.abspath(OlivOSAIChatAssassin.data.gImageDir)
    if not os.path.isdir(image_dir):
        return
    referenced_files = get_referenced_image_files()
    now = time.time()
    for file_name in os.listdir(image_dir):
        if file_name in referenced_files:
            continue
        file_path = os.path.abspath(os.path.join(image_dir, file_name))
        image_dir_with_sep = image_dir + os.sep
        if not file_path.startswith(image_dir_with_sep) or not os.path.isfile(file_path):
            continue
        try:
            if now - os.path.getmtime(file_path) > cleanup_time:
                os.remove(file_path)
                OlivOSAIChatAssassin.logger.log(f'IMAGE CLEANUP ORPHAN - {file_name}')
        except Exception as e:
            OlivOSAIChatAssassin.logger.warn(f'IMAGE CLEANUP ORPHAN ERR - {file_name}: {e}')


def cleanup_runtime_image_cache() -> None:
    image_dir = os.path.abspath(OlivOSAIChatAssassin.data.gImageDir)
    for group_id, cache_queue in list(OlivOSAIChatAssassin.data.gImageCache.items()):
        keep_items = deque(maxlen=cache_queue.maxlen)
        for item in cache_queue:
            if not (isinstance(item, tuple) and len(item) >= 1):
                continue
            file_path = os.path.abspath(os.path.join(image_dir, str(item[0])))
            if os.path.exists(file_path):
                keep_items.append(item)
        OlivOSAIChatAssassin.data.gImageCache[group_id] = keep_items


def msg_trans(msg: str, group_id: str, *, plugin_event: 'OlivOS.API.Event|None' = None, bot_hash: str):
    res = msg

    if plugin_event is not None:
        res = expand_forward_messages(plugin_event, res)

    if '[OP:image' not in res and ':mface,' not in res:
        return res
    cleanup_non_emoji_images(bot_hash)

    def resolve_media_cache(file_name: str, image_url: 'str|None', message_text: str, image_type: str, summary: 'str|None' = None):
        res_data = None
        global_image_cache = OlivOSAIChatAssassin.data.gData.getMemory(bot_hash).get('全局', {}).get('图片缓存', {})
        if isinstance(global_image_cache, dict):
            cache_data = global_image_cache.get(file_name)
            if isinstance(cache_data, dict):
                res_data = cache_data
        if res_data is None and image_url and OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
            'ocr_api',
            OlivOSAIChatAssassin.data.configDefault['ocr_api']
        ).get(
            'enable',
            OlivOSAIChatAssassin.data.configDefault['ocr_api']['enable']
        ):
            image_input = image_url
            if OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get('ocr_api', {}).get('mode', 'base64') == 'base64':
                image_input = OlivOSAIChatAssassin.webTools.download_image_to_base64(
                    url=image_url,
                    save_dir=OlivOSAIChatAssassin.data.gImageDir,
                    filename=file_name
                ) or image_url
            examples_reply = {
                'content': '内容描述',
                'intent': '意图描述',
                'type': '类型描述'
            }
            prompt = f'''
# 当前任务
## 识别图片并分析相关信息，以Json格式输出
- 用户正在群聊中聊天，请识别图片并分析相关信息
- 尽量精确的描述这个图片的内容，32字以内，单行内，输出到 "content" 键的值中
- 如果有角色，识别的内容也应该尽量识别角色
- 为用户分析这张图片的意图，32字以内，单行内，输出到 "intent" 键的值中
- 分析这个图片的类型，例如表情包、梗图、照片、普通图片，输出到 "type" 键的值中
- 以JSON格式输出
# 参考输出，以严格的Json格式输出
{json.dumps(examples_reply, ensure_ascii=False)}
'''
            try:
                ocr_res_data = OlivOSAIChatAssassin.webTools.call_ai_ocr(
                    OlivOSAIChatAssassin.data.gData.getConfig(bot_hash),
                    prompt=prompt,
                    image_url=image_input
                )
                if isinstance(ocr_res_data, str):
                    ocr_res = json.loads(ocr_res_data)
                    if isinstance(ocr_res, dict) and all(isinstance(ocr_res.get(key), str) for key in examples_reply):
                        OlivOSAIChatAssassin.data.gData.getMemory(bot_hash).setdefault('全局', {})
                        OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)['全局'].setdefault('图片缓存', {})
                        OlivOSAIChatAssassin.data.gData.getMemory(bot_hash)['全局']['图片缓存'][file_name] = ocr_res
                        OlivOSAIChatAssassin.load.write_memory(bot_hash=bot_hash)
                        res_data = ocr_res
            except Exception as e:
                OlivOSAIChatAssassin.logger.warn(f'CALL AI OCR - ERR: {e}')
        if res_data is None:
            res_data = build_basic_image_cache_data(message_text, image_type=image_type, summary=summary)
        OlivOSAIChatAssassin.data.gImageCache.setdefault(
            group_id,
            deque(
                maxlen=OlivOSAIChatAssassin.data.gData.getConfig(bot_hash).get(
                    'ocr_api',
                    OlivOSAIChatAssassin.data.configDefault['ocr_api']
                ).get(
                    'queue_size',
                    OlivOSAIChatAssassin.data.configDefault['ocr_api']['queue_size']
                )
            )
        )
        OlivOSAIChatAssassin.data.gImageCache[group_id].append((file_name, res_data))
        return OlivOSAIChatAssassin.tools.imgcode_format(res_data)

    def process_image(match: re.Match) -> str:
        original = match.group(0)
        params = OlivOSAIChatAssassin.tools.opcode_parse_params('image', original)
        file_name = params.get('file')
        if not isinstance(file_name, str) or len(file_name) <= 0:
            return OlivOSAIChatAssassin.tools.imgcode_format()
        return resolve_media_cache(
            file_name,
            params.get('url'),
            original,
            '图片'
        )

    def process_mface(match: re.Match) -> str:
        original = match.group(0)
        file_name = get_mface_file_name(original)
        summary = extract_tag_param(original, 'summary')
        return resolve_media_cache(
            file_name,
            extract_tag_param(original, 'url'),
            summary or original,
            '表情包',
            summary=summary
        )

    res = OP_IMAGE_PATTERN.sub(process_image, res)
    res = MFACE_PATTERN.sub(process_mface, res)
    return res


def msg_wash(msg: str):
    return clean_media_tags(msg)
