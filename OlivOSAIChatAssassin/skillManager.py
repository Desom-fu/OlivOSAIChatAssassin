"""
skillManager.py - OlivOSAIChatAssassin 技能动态检索系统

功能:
  1. 递归索引 data/skills/ 目录下所有含 SKILL.md 的子目录
  2. 根据聊天上下文智能匹配相关技能
  3. 对大文件进行切片读取,只截取相关段落
  4. 将技能片段动态注入到 LLM 的 System Prompt 中

设计原则:
  - 零外部依赖 (不引入 PyYAML / jieba 等)
  - 异常安全,绝不中断主聊天流程
  - Token 预算控制,严禁全量堆砌
"""

import os
import re
import time
import hashlib
from typing import Optional

import OlivOSAIChatAssassin


# ============================================================
# 常量
# ============================================================

# YAML frontmatter 正则:匹配 ^--- 之间的内容
_FRONTMATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n', re.DOTALL)
# frontmatter 内的 key: value 提取
_FRONTMATTER_KEY_RE = re.compile(r'^(\w+):\s*(.+?)(?:\n(?=\w)|\Z)', re.MULTILINE | re.DOTALL)
# Markdown 标题正则 (## 和 ###)
_HEADER_RE = re.compile(r'^(#{2,3})\s+(.+?)\s*$', re.MULTILINE)
# Markdown 表格行正则
_TABLE_ROW_RE = re.compile(r'^\|(.+?)\|\s*$', re.MULTILINE)
# 引用文件路径正则 (匹配 `references/xxx.md` 等模式)
_REF_FILE_RE = re.compile(r'[`\"\']?(references?/[^\s`\"\']+\.md)[`\"\']?', re.IGNORECASE)
# media tag 正则 (用于清理聊天消息)
_MEDIA_TAG_RE = re.compile(r'\[(?:OP|CQ):[^\]]+\]')
# 中文停用词
_STOP_WORDS = frozenset([
    # 1-char function words
    '的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '这', '那', '有',
    '不', '就', '都', '也', '还', '又', '才', '只', '要', '会', '能', '把', '被',
    '让', '给', '对', '跟', '和', '与', '或', '但', '可', '以', '上', '下', '里',
    '去', '来', '到', '说', '看', '想', '做', '吧', '呢', '啊', '哦', '嗯', '哈',
    '一', '个', '什', '么', '怎', '样', '哪', '谁', '哪', '多', '少', '很', '太',
    '好', '坏', '对', '错', '没', '无', '有', '为', '着', '过', '地', '得', '而',
    '则', '若', '如', '其', '此', '些', '某', '每', '各', '另', '再', '已', '正',
    '将', '已', '曾', '须', '应', '该', '得', '即', '便', '然', '虽', '因', '故',
    # 2-char common function/conversational words (filter from reference search)
    '什么', '怎么', '如何', '为何', '怎样', '哪里', '哪个', '多少',
    '大家', '我们', '你们', '他们', '自己', '它们', '别人', '人家',
    '可以', '可能', '应该', '需要', '就是', '也是', '不是', '还是',
    '已经', '只有', '只要', '如果', '虽然', '但是', '因为', '所以',
    '不过', '而且', '或者', '里面', '外面', '上面', '下面', '前面',
    '后面', '这个', '那个', '这些', '那些', '这里', '那里', '时候',
    '现在', '之前', '之后', '以后', '以前', '好的', '好吧', '我想',
    '我要', '我会', '我能', '的话', '一样', '一般', '一下', '一种',
])

# 小文件阈值 (字符数): 低于此值整包读取
_SMALL_FILE_THRESHOLD = 1500
# 切片上下文扩展行数
_SLICE_CONTEXT_LINES = 5
# 关键词最大长度 (字符): 中文有意义词通常 2-4 字
_KW_MAX_LEN = 4
# 关键词最小长度 (字符)
_KW_MIN_LEN = 2
# 上下文消息条数
_CONTEXT_MSG_COUNT = 5
# 返回关键词上限
_KW_TOP_N = 40

# 缓存TTL (秒),复用插件的 search_ageing 配置
_CACHE_TTL_DEFAULT = 900


# ============================================================
# 模块级缓存系统
# ============================================================
# 四级缓存:keyword / match / rank / file,带TTL自动过期
# 缓存键设计:keyword用消息文本hash,match用关键词hash,rank用(word1,word2)元组,file用path+mtime

_skills_cache: 'dict' = {
    'keyword': {},   # msg_hash -> (keywords_list, timestamp)
    'match': {},     # kw_hash -> (matches_list, timestamp)
    'rank': {},      # (word1, word2) -> rank_int (永久,不过期,因为纯函数)
    'file': {},      # path -> (content_str, mtime_float)
}


def _cache_get(cache_name: str, key: str, ttl: int) -> 'object|None':
    """从指定缓存层获取值,过期返回None"""
    layer = _skills_cache.get(cache_name, {})
    entry = layer.get(key)
    if entry is None:
        return None
    if cache_name in ('keyword', 'match'):
        # 带TTL的缓存
        cached_data, timestamp = entry
        if time.time() - timestamp > ttl:
            layer.pop(key, None)
            return None
        return cached_data
    return entry


def _cache_set(cache_name: str, key: str, value):
    """写入指定缓存层"""
    if cache_name in ('keyword', 'match'):
        _skills_cache[cache_name][key] = (value, time.time())
    else:
        _skills_cache[cache_name][key] = value


def _cached_rank(word1: str, word2: str, rate: float) -> int:
    """带缓存的 get_recommendRank:同输入永远返回同结果,可永久缓存"""
    # rate 影响结果,需要纳入key
    cache_key = f'{word1}\x00{word2}\x00{rate}'
    cached = _skills_cache['rank'].get(cache_key)
    if cached is not None:
        return cached
    result = OlivOSAIChatAssassin.tools.get_recommendRank(word1, word2, rate=rate)
    _skills_cache['rank'][cache_key] = result
    return result


def _cached_file_read(path: str) -> 'str|None':
    """带mtime校验的文件读取缓存:文件未改则返回缓存内容"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _skills_cache['file'].get(path)
    if cached is not None:
        cached_content, cached_mtime = cached
        if cached_mtime == mtime:
            return cached_content
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        _skills_cache['file'][path] = (content, mtime)
        return content
    except Exception:
        return None


def clear_cache():
    """清空所有缓存(索引重建时调用)"""
    _skills_cache['keyword'].clear()
    _skills_cache['match'].clear()
    _skills_cache['rank'].clear()
    _skills_cache['file'].clear()


# ============================================================
# 1. 递归索引
# ============================================================

def build_skills_index() -> dict:
    """
    递归扫描 data/skills/ 目录,对每个含 SKILL.md 的子目录建立索引条目。

    返回:
        dict: {skill_name: index_entry, ...}
    """
    clear_cache()
    skills_dir = OlivOSAIChatAssassin.data.gSkillsDir
    index = {}

    try:
        os.makedirs(skills_dir, exist_ok=True)
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'[Skills] 创建技能目录失败: {e}')
        return index

    try:
        for root, dirs, files in os.walk(skills_dir):
            if 'SKILL.md' in files:
                skill_md_path = os.path.join(root, 'SKILL.md')
                entry = _parse_skill_md(skill_md_path, root)
                if entry is not None:
                    name = entry.get('name', '')
                    if name:
                        index[name] = entry
                        OlivOSAIChatAssassin.logger.log(f'[Skills] 已索引技能: {name} ({len(entry.get("headers", []))} 个标题)')
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'[Skills] 扫描技能目录失败: {e}')

    OlivOSAIChatAssassin.logger.log(f'[Skills] 索引完成,共 {len(index)} 个技能')
    return index


def _parse_skill_md(skill_md_path: str, skill_dir: str) -> 'Optional[dict]':
    """
    解析单个 SKILL.md 文件,构建索引条目。

    参数:
        skill_md_path: SKILL.md 绝对路径
        skill_dir: 技能目录路径

    返回:
        dict 或 None (解析失败时)
    """
    try:
        with open(skill_md_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'[Skills] 读取 SKILL.md 失败 [{skill_md_path}]: {e}')
        return None

    # 解析 YAML frontmatter
    name, description = _parse_frontmatter(content)
    if not name:
        OlivOSAIChatAssassin.logger.warn(f'[Skills] SKILL.md 缺少 name 字段 [{skill_md_path}]')
        return None

    # 预解析标题
    headers = _parse_headers(content)

    # 解析导航表
    nav_table = _parse_nav_table(content)

    # 提取引用文件路径
    references = _extract_references(content)

    return {
        'name': name,
        'description': description or '',
        'desc_text': f"{name} {description or ''}",
        'dir': skill_dir,
        'skill_md': skill_md_path,
        'headers': headers,
        'nav_table': nav_table,
        'references': references,
    }


def _parse_frontmatter(content: str) -> 'tuple[str, str]':
    """
    从 SKILL.md 内容中解析 YAML frontmatter 的 name 和 description 字段。
    不引入 PyYAML,用简单正则提取。
    """
    name = ''
    description = ''

    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        fm_text = fm_match.group(1)
        # 提取 name (可能带引号)
        name_match = re.search(r'^name:\s*(.+?)\s*$', fm_text, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip().strip('"\'')
        # 提取 description (可能跨行,取到下一个 key 或末尾)
        desc_match = re.search(r'^description:\s*(.+?)(?=\n\w+:|\Z)', fm_text, re.MULTILINE | re.DOTALL)
        if desc_match:
            description = desc_match.group(1).strip().strip('"\'')

    return name, description


def _parse_headers(content: str) -> 'list[dict]':
    """
    预解析 Markdown 标题 (## 和 ###),记录标题文本和行号。
    """
    headers = []
    for match in _HEADER_RE.finditer(content):
        level = len(match.group(1))
        title = match.group(2).strip()
        line = content[:match.start()].count('\n') + 1
        headers.append({
            'title': title,
            'line': line,
            'level': level,
        })
    return headers


def _parse_nav_table(content: str) -> 'list[dict]':
    """
    解析 Markdown 导航表格 (如有)。
    识别格式:
    | 用户问题 | 读取章节 |
    | --- | --- |
    | xxx | yyy |
    """
    nav_table = []
    lines = content.split('\n')
    in_table = False
    header_row = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('|'):
            if in_table and header_row is not None:
                break
            continue

        # 解析表格行
        cells = [c.strip() for c in stripped.split('|')[1:-1]]
        if len(cells) < 2:
            continue

        if header_row is None:
            # 第一行是表头
            header_row = cells
            in_table = True
            continue

        # 跳过分隔行 (| --- | --- |)
        if all(re.match(r'^[-:]+$', c) for c in cells):
            continue

        # 数据行
        entry = {}
        for j, cell in enumerate(cells):
            if j < len(header_row):
                entry[header_row[j]] = cell
        if entry:
            nav_table.append(entry)

    return nav_table


def _extract_references(content: str) -> 'list[str]':
    """
    从 SKILL.md 内容中提取引用文件路径 (如 references/rulebook.md)。
    """
    refs = []
    for match in _REF_FILE_RE.finditer(content):
        ref = match.group(1).strip().strip('`"\'')
        if ref and ref not in refs:
            refs.append(ref)
    return refs


# ============================================================
# 2. 上下文关键词提取
# ============================================================

def extract_context_keywords(history: 'list[dict]') -> 'list[str]':
    """
    从最近 N 条聊天历史中提取关键词。

    使用轻量级中文滑动窗口分词:
    - 提取 2-4 字的连续中文/英文词组
    - 子串去重:若短词是长词的子串且频率相同,移除短词
    - 按 freq * len 综合排序
    - 过滤停用词

    参数:
        history: 聊天历史列表 (每个元素是 dict,含 message 字段)

    返回:
        list[str]: 关键词列表,按相关性排序
    """
    if not history:
        return []

    # 合并最近 N 条消息文本
    recent = history[-_CONTEXT_MSG_COUNT:] if len(history) > _CONTEXT_MSG_COUNT else history
    combined_text = ''
    for entry in recent:
        msg = entry.get('message', '')
        if not isinstance(msg, str):
            continue
        # 清理 media tag
        msg = _MEDIA_TAG_RE.sub('', msg)
        combined_text += msg + '\n'

    if not combined_text.strip():
        return []

    # 滑动窗口提取候选词
    candidates: dict[str, int] = {}
    lines = combined_text.split('\n')
    for text in lines:
        _extract_ngrams(text, candidates)

    # 过滤停用词
    filtered = [
        (kw, freq) for kw, freq in candidates.items()
        if kw not in _STOP_WORDS and len(kw) >= _KW_MIN_LEN
    ]

    # 子串去重:若短词是长词的子串且长词频率严格更高,移除短词
    # 同频率时不移除(短词可能是有意义的独立词,如"红烧肉" vs "做红烧肉")
    # 按长度降序排列,优先保留长词
    filtered.sort(key=lambda x: (-len(x[0]), -x[1]))
    deduped = []
    for kw, freq in filtered:
        is_substring = False
        for existing_kw, existing_freq in deduped:
            if kw in existing_kw and freq < existing_freq:
                is_substring = True
                break
        if not is_substring:
            deduped.append((kw, freq))

    # 按 freq * len 综合排序 (频率和长度都重要)
    deduped.sort(key=lambda x: -(x[1] * len(x[0])))

    # 返回关键词列表
    return [kw for kw, _ in deduped[:_KW_TOP_N]]


def _extract_ngrams(text: str, candidates: 'dict[str, int]'):
    """
    从文本中提取 n-gram 候选词 (2-6 字)。
    中文连续字符和英文单词都会被提取。
    """
    # 提取连续的中文段和英文单词段
    segments = re.findall(r'[\u4e00-\u9fff]{2,20}|[a-zA-Z]{3,30}', text)
    for seg in segments:
        seg_len = len(seg)
        if seg_len < _KW_MIN_LEN:
            continue
        # 中文: 提取 2-6 字的滑动窗口
        if re.match(r'^[\u4e00-\u9fff]+$', seg):
            max_n = min(_KW_MAX_LEN, seg_len)
            for n in range(_KW_MIN_LEN, max_n + 1):
                for i in range(seg_len - n + 1):
                    ngram = seg[i:i + n]
                    if ngram not in _STOP_WORDS:
                        candidates[ngram] = candidates.get(ngram, 0) + 1
        else:
            # 英文单词直接作为候选
            candidates[seg.lower()] = candidates.get(seg.lower(), 0) + 1


# ============================================================
# 3. 智能匹配
# ============================================================

def match_skills(
    keywords: 'list[str]',
    bot_hash: str
) -> 'list[dict]':
    """
    三层匹配策略,返回排序后的技能匹配列表。

    参数:
        keywords: 上下文关键词列表
        bot_hash: 机器人哈希

    返回:
        list[dict]: [{entry, score, matched_headers, matched_nav}, ...]
    """
    skills_index = OlivOSAIChatAssassin.data.gSkillsIndex
    if not skills_index or not keywords:
        return []

    config = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
    rate = config.get(
        'skills_match_rate',
        OlivOSAIChatAssassin.data.configDefault['skills_match_rate']
    )
    max_matches = config.get(
        'skills_max_matches',
        OlivOSAIChatAssassin.data.configDefault['skills_max_matches']
    )

    results = []

    for skill_name, entry in skills_index.items():
        score = 0
        matched_headers = []
        matched_nav = []

        # --- Layer 1: 技能名称/描述匹配 ---
        desc_text = entry.get('desc_text', f"{entry.get('name', '')} {entry.get('description', '')}")
        for kw in keywords:
            # 用 _cached_rank 做带缓存的模糊匹配
            if len(kw) <= len(desc_text):
                rank = _cached_rank(kw, desc_text, rate)
            else:
                rank = _cached_rank(desc_text, kw, rate)
            if OlivOSAIChatAssassin.tools.get_recommendMatch(rank):
                score += 1

        # --- Layer 2: 标题匹配 ---
        for header in entry.get('headers', []):
            header_title = header.get('title', '')
            for kw in keywords:
                if kw in header_title or header_title in kw:
                    matched_headers.append(header)
                    score += 2  # 标题匹配权重更高
                    break
                # 模糊匹配标题
                if len(kw) <= len(header_title):
                    rank = _cached_rank(kw, header_title, rate)
                else:
                    rank = _cached_rank(header_title, kw, rate)
                if OlivOSAIChatAssassin.tools.get_recommendMatch(rank):
                    matched_headers.append(header)
                    score += 2
                    break

        # --- Layer 3: 导航表匹配 ---
        for nav_entry in entry.get('nav_table', []):
            # 导航表的值 (第一列通常是问题/关键词,第二列是章节)
            nav_values = list(nav_entry.values())
            for kw in keywords:
                hit = False
                for val in nav_values:
                    if kw in val or val in kw:
                        matched_nav.append(nav_entry)
                        score += 3  # 导航表匹配权重最高
                        hit = True
                        break
                if hit:
                    break

        results.append({
            'entry': entry,
            'score': score,
            'matched_headers': matched_headers,
            'matched_nav': matched_nav,
            'keywords': keywords,
        })

    # 过滤掉 score=0 的结果(三层匹配均未命中),按分数排序,截断到 max_matches
    results = [r for r in results if r['score'] > 0]
    results.sort(key=lambda x: -x['score'])
    return results[:max_matches]


# ============================================================
# 4. 高效切片
# ============================================================

def slice_skill_content(
    match_result: dict,
    budget_chars: int
) -> str:
    """
    对匹配的技能进行切片读取。

    参数:
        match_result: match_skills 返回的单个匹配结果
        budget_chars: 本次切片的字符预算

    返回:
        str: 格式化的技能片段
    """
    entry = match_result['entry']
    skill_md_path = entry['skill_md']
    skill_name = entry.get('name', 'unknown')
    description = entry.get('description', '')
    matched_headers = match_result.get('matched_headers', [])
    matched_nav = match_result.get('matched_nav', [])

    try:
        full_content = _cached_file_read(skill_md_path)
        if full_content is None:
            OlivOSAIChatAssassin.logger.warn(f'[Skills] 切片读取失败 [{skill_name}]')
            return ''
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'[Skills] 切片读取失败 [{skill_name}]: {e}')
        return ''

    # 去除 frontmatter
    body = _FRONTMATTER_RE.sub('', full_content, count=1)
    body = body.strip()

    # 始终包含的摘要头
    header = f"[Skill: {skill_name}]"
    if description:
        header += f"\n{description[:200]}"
    header_len = len(header)

    remaining_budget = budget_chars - header_len
    if remaining_budget <= 0:
        return header[:budget_chars]

    # --- 小文件: 整包读取 ---
    if len(body) <= _SMALL_FILE_THRESHOLD:
        result = header + '\n' + body[:remaining_budget]
        if len(body) > remaining_budget:
            result += '\n[...截断...]'
        return result

    # --- 大文件: 按标题切片 ---
    lines = body.split('\n')
    total_lines = len(lines)

    # 如果没有命中标题,尝试导航表匹配的章节
    target_headers = list(matched_headers)
    if not target_headers and matched_nav:
        # 从导航表的 section 值反查标题
        for nav in matched_nav:
            nav_values = list(nav.values())
            for val in nav_values:
                for h in entry.get('headers', []):
                    if val in h.get('title', '') or h.get('title', '') in val:
                        if h not in target_headers:
                            target_headers.append(h)

    if not target_headers:
        # 没有命中标题,读取前 N 行作为摘要 (但不立即返回,继续检查引用文件)
        chunk_lines = lines[:min(20, total_lines)]
        chunk = '\n'.join(chunk_lines)[:remaining_budget]
        chunks = [chunk]
        remaining_budget -= len(chunk) + 2
        if remaining_budget < 0:
            remaining_budget = 0
    else:
        # 按命中标题切片
        chunks = []
        used_lines = set()
        for h in target_headers:
            if remaining_budget <= 0:
                break
            title = h.get('title', '')
            h_line = h.get('line', 0)
            h_level = h.get('level', 2)

            # 找到标题在 body 中的行号 (frontmatter 已去除,需要调整)
            # body 的行号从 0 开始,header 的 line 从原文(含frontmatter)开始
            # 重新搜索标题位置
            start_idx = _find_header_line_index(lines, title, h_level)
            if start_idx < 0:
                continue

            # 找到下一个同级或更高级标题
            end_idx = _find_next_header(lines, start_idx, h_level)

            # 扩展上下文
            ctx_start = max(0, start_idx - _SLICE_CONTEXT_LINES)
            ctx_end = min(total_lines, end_idx + _SLICE_CONTEXT_LINES)

            # 收集未使用的行
            chunk_lines = []
            for i in range(ctx_start, ctx_end):
                if i not in used_lines:
                    chunk_lines.append(lines[i])
                    used_lines.add(i)

            if chunk_lines:
                chunk_text = '\n'.join(chunk_lines)
                if len(chunk_text) > remaining_budget:
                    chunk_text = chunk_text[:remaining_budget] + '\n[...截断...]'
                chunks.append(chunk_text)
                remaining_budget -= len(chunk_text) + 2  # +2 for \n\n

    # --- 关键词回退搜索:在引用文件中搜索包含上下文关键词的章节 ---
    # 当导航表未命中具体章节时(如用户问"反击"但导航表只有"战斗"),直接搜索引用文件
    # 优先于导航表切片,因为关键词匹配更精确
    keywords = match_result.get('keywords', [])
    if keywords and remaining_budget > 0 and entry.get('references'):
        # 传入技能描述,用于过滤出现在描述中的宽泛关键词(如"狩魂"、"魂者")
        skill_desc = entry.get('desc_text', '')
        for ref_path in entry.get('references', []):
            if remaining_budget <= 0:
                break
            ref_full = os.path.join(entry['dir'], ref_path)
            kw_chunk = _search_reference_for_keywords(ref_full, keywords, remaining_budget, skill_desc)
            if kw_chunk:
                chunks.append(kw_chunk)
                remaining_budget -= len(kw_chunk) + 2

    # --- 导航表指向的引用文件切片 ---
    # 限制最多 3 条导航条目,且不超过剩余预算的一半
    if matched_nav and remaining_budget > 0:
        nav_budget = remaining_budget // 2
        for nav in matched_nav[:3]:
            if nav_budget <= 0:
                break
            nav_values = list(nav.values())
            # 导航表第二列通常是章节标题,去除反引号等 markdown 标记
            section_title = nav_values[1] if len(nav_values) > 1 else ''
            if not section_title:
                continue
            section_title = section_title.strip('`"\' ')
            # 在引用文件中搜索该章节
            for ref_path in entry.get('references', []):
                if nav_budget <= 0:
                    break
                ref_full = os.path.join(entry['dir'], ref_path)
                ref_chunk = _slice_reference_file(ref_full, section_title, nav_budget)
                if ref_chunk:
                    chunk_len = len(ref_chunk) + 2
                    chunks.append(ref_chunk)
                    nav_budget -= chunk_len
                    remaining_budget -= chunk_len

    if not chunks:
        # 没有切出任何内容,回退到前 20 行
        chunk = '\n'.join(lines[:min(20, total_lines)])[:remaining_budget]
        return header + '\n' + chunk

    return header + '\n' + '\n'.join(chunks)


def _find_header_line_index(lines: 'list[str]', title: str, level: int) -> int:
    """
    在 body 行列表中查找指定标题的行索引。
    """
    prefix = '#' * level + ' '
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(prefix) and title in stripped:
            return i
    return -1


def _find_next_header(lines: 'list[str]', start_idx: int, level: int) -> int:
    """
    找到 start_idx 之后下一个同级或更高级标题的行索引。
    """
    prefix_any = '#' * level + ' '
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith('#' * 1 + ' ') and not stripped.startswith('#' * (level + 1) + ' '):
            # 找到同级或更高级标题
            if stripped.startswith(prefix_any) or (
                stripped.startswith('#' * (level - 1) + ' ') and level > 2
            ):
                return i
    return len(lines)


def _slice_reference_file(
    ref_path: str,
    section_title: str,
    budget: int
) -> str:
    """
    从引用文件中切片指定章节。
    """
    content = _cached_file_read(ref_path)
    if content is None:
        return ''

    lines = content.split('\n')
    total = len(lines)

    # 搜索包含 section_title 的标题
    start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('#') and section_title in stripped:
            start_idx = i
            break

    if start_idx < 0:
        return ''

    # 确定标题级别
    level = 0
    while start_idx < total and lines[start_idx].strip()[level:level+1] == '#':
        level += 1

    # 找到下一个同级或更高级标题
    end_idx = _find_next_header(lines, start_idx, max(level, 2))

    # 扩展上下文
    ctx_start = max(0, start_idx - _SLICE_CONTEXT_LINES)
    ctx_end = min(total, end_idx + _SLICE_CONTEXT_LINES)

    chunk_lines = lines[ctx_start:ctx_end]
    chunk = '\n'.join(chunk_lines)

    if len(chunk) > budget:
        chunk = chunk[:budget] + '\n[...截断...]'

    return chunk


def _search_reference_for_keywords(
    ref_path: str,
    keywords: 'list[str]',
    budget: int,
    skill_desc: str = ''
) -> str:
    """
    在引用文件中搜索包含关键词的章节,返回匹配的章节片段。
    当导航表匹配未命中具体章节时的回退机制。

    参数:
        ref_path: 引用文件路径
        keywords: 上下文关键词列表
        budget: 字符预算
        skill_desc: 技能名称+描述,用于过滤宽泛的短关键词

    返回:
        str: 匹配的章节内容,或空字符串
    """
    # 关键词过滤:
    # - 3 字以上的关键词:保留(足够具体)
    # - 2 字的关键词:仅当不出现在技能描述中时保留(如"反击"是具体术语,而"狩魂"是宽泛词)
    search_kws = []
    for kw in keywords:
        if len(kw) >= 3:
            search_kws.append(kw)
        elif len(kw) == 2 and skill_desc and kw not in skill_desc:
            search_kws.append(kw)

    # 从 3+ 字关键词中提取 2 字子串,补充可能被 freq*len 排序截断的短关键词
    # 例如用户消息含"反击规则",ngram提取出"反击规"(3字)但"反击"(2字)可能被topN截断
    # 从"反击规"提取"反击",即可在规则书中匹配到"反击"相关章节
    two_char_subs = set()
    for kw in search_kws:
        if len(kw) >= 3 and re.match(r'^[\u4e00-\u9fff]+$', kw):
            for i in range(len(kw) - 1):
                sub = kw[i:i + 2]
                if skill_desc and sub not in skill_desc and sub not in _STOP_WORDS:
                    two_char_subs.add(sub)
    search_kws.extend(two_char_subs)

    # 过滤含技能描述子串的 3+ 字中文关键词
    # 如"玩狩魂者"含"狩魂"(在描述中),这类词是宽泛的游戏相关词而非具体规则术语
    if skill_desc:
        filtered = []
        for kw in search_kws:
            if len(kw) >= 3 and re.match(r'^[\u4e00-\u9fff]+$', kw):
                has_desc_sub = any(kw[i:i + 2] in skill_desc for i in range(len(kw) - 1))
                if has_desc_sub:
                    continue
            filtered.append(kw)
        search_kws = filtered

    if not search_kws:
        return ''

    content = _cached_file_read(ref_path)
    if content is None:
        return ''

    lines = content.split('\n')
    total = len(lines)

    # 找到所有 ## 和 ### 标题的位置
    sections = []  # [(start_idx, end_idx, title)]
    header_indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^#{2,3}\s+', stripped):
            header_indices.append(i)

    for idx, h_start in enumerate(header_indices):
        h_end = header_indices[idx + 1] if idx + 1 < len(header_indices) else total
        title = lines[h_start].strip()
        sections.append((h_start, h_end, title))

    if not sections:
        return ''

    # 预计算各章节文本 (避免重复 join)
    section_texts = ['\n'.join(lines[s:e]) for s, e, _ in sections]

    # 基于词频的章节匹配:关键词在章节中出现次数越多,该章节越可能是用户所需的
    # 如"反击"在"战斗"章节出现 8 次,说明该章节重点讨论反击规则
    # 而闲聊词"我想"在整个规则书中仅出现 2-3 次,自然被排在后面
    matched_sections = []
    for i, (start, end, title) in enumerate(sections):
        st = section_texts[i]
        best_kw = None
        best_tf = 0
        for kw in search_kws:
            tf = st.count(kw)
            if tf > best_tf:
                best_tf = tf
                best_kw = kw
        if best_kw:
            matched_sections.append((start, end, title, best_kw, best_tf))

    if not matched_sections:
        return ''

    # 按词频降序排序(出现次数最多的章节优先),取前 3 个章节
    matched_sections.sort(key=lambda x: (-x[4], x[0]))
    matched_sections = matched_sections[:3]

    # 按词频比例预分配预算,确保每个匹配章节都能分到字符
    # 否则第一个大章节(如200+行的术法)会吞掉全部预算,后面的章节无从切片
    total_tf = sum(ms[4] for ms in matched_sections) or 1

    chunks = []
    remaining = budget
    for start, end, title, kw, tf in matched_sections:
        if remaining <= 0:
            break
        # 此章节的预算 = 总预算 × (此章节TF / 总TF),最少200字符
        section_budget = min(remaining, max(200, int(budget * tf / total_tf)))

        # 在章节内以关键词为中心切片,而非从章节开头截断
        # 这样即使章节很大(200+行),也能确保关键词附近的文本出现在输出中
        section_lines = lines[start:end]
        section_text = '\n'.join(section_lines)
        kw_pos = section_text.find(kw)

        if kw_pos >= 0:
            # 将字符位置转换为章节内行号
            kw_line = section_text[:kw_pos].count('\n')
            # 以关键词所在行为中心,前后各取约 section_budget//80 行
            half_lines = max(5, section_budget // 80)
            win_start = max(0, kw_line - half_lines)
            win_end = min(len(section_lines), kw_line + half_lines + 5)
            chunk_lines = section_lines[win_start:win_end]
            chunk_text = '\n'.join(chunk_lines)
            # 窗口未从章节标题开始时,在前面补上标题行供上下文
            if win_start > 0:
                chunk_text = lines[start].strip() + '\n...\n' + chunk_text
        else:
            # 回退:从章节开头读取
            chunk_text = section_text[:section_budget]

        if len(chunk_text) > section_budget:
            chunk_text = chunk_text[:section_budget] + '\n[...截断...]'

        chunks.append(chunk_text)
        remaining -= len(chunk_text) + 2  # +2 for \n\n

    return '\n\n'.join(chunks) if chunks else ''


# ============================================================
# 5. 主接口
# ============================================================

def get_skills_context(history: 'list[dict]', bot_hash: str) -> str:
    """
    从聊天历史中检索相关技能,返回格式化的技能片段字符串。

    参数:
        history: 聊天历史列表
        bot_hash: 机器人哈希

    返回:
        str: 格式化的技能片段,或空字符串 (无匹配时)
    """
    try:
        # 检查是否启用
        config = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
        if not config.get(
            'skills_enable',
            OlivOSAIChatAssassin.data.configDefault['skills_enable']
        ):
            return ''

        # 检查索引是否为空
        if not OlivOSAIChatAssassin.data.gSkillsIndex:
            return ''

        # 获取缓存TTL
        ttl = config.get(
            'search_ageing',
            OlivOSAIChatAssassin.data.configDefault['search_ageing']
        )

        # --- 关键词提取 (带缓存) ---
        # 用消息文本的hash作为缓存键,相同消息直接返回缓存的关键词
        recent = history[-_CONTEXT_MSG_COUNT:] if len(history) > _CONTEXT_MSG_COUNT else history
        msg_parts = []
        for entry in recent:
            msg = entry.get('message', '')
            if isinstance(msg, str):
                msg_parts.append(_MEDIA_TAG_RE.sub('', msg))
        msg_hash = hashlib.md5('\n'.join(msg_parts).encode('utf-8')).hexdigest()

        cached_keywords = _cache_get('keyword', msg_hash, ttl)
        if cached_keywords is not None:
            keywords = cached_keywords
        else:
            keywords = extract_context_keywords(history)
            _cache_set('keyword', msg_hash, keywords)

        if not keywords:
            return ''

        OlivOSAIChatAssassin.logger.log(f'[Skills] 上下文关键词: {keywords[:10]}')

        # --- 技能匹配 (带缓存) ---
        # 用关键词列表的hash作为缓存键,相同关键词直接返回缓存的匹配结果
        kw_hash = hashlib.md5('|'.join(sorted(keywords)).encode('utf-8')).hexdigest()

        cached_matches = _cache_get('match', kw_hash, ttl)
        if cached_matches is not None:
            matches = cached_matches
        else:
            matches = match_skills(keywords, bot_hash)
            _cache_set('match', kw_hash, matches)

        if not matches:
            return ''

        OlivOSAIChatAssassin.logger.log(f'[Skills] 匹配到 {len(matches)} 个技能: {[m["entry"]["name"] for m in matches]}')

        # Token 预算分配
        max_chars = config.get(
            'skills_max_chars',
            OlivOSAIChatAssassin.data.configDefault['skills_max_chars']
        )

        # 按匹配分数比例分配预算
        total_score = sum(m['score'] for m in matches)
        if total_score <= 0:
            total_score = 1

        chunks = []
        for match in matches:
            budget = int(max_chars * match['score'] / total_score)
            if budget < 200:
                budget = 200
            chunk = slice_skill_content(match, budget)
            if chunk:
                chunks.append(chunk)

        if not chunks:
            return ''

        # 前导换行:技能片段位于 prompt 末尾,前导 \n 与前面的固定记忆隔开一空行
        # 空字符串时 get_skills_context 返回 '' (此处不会执行到),非空时才加前导换行
        result = '\n# 技能知识\n' + '\n\n'.join(chunks)

        # 最终安全截断
        if len(result) > max_chars:
            result = result[:max_chars] + '\n[...截断...]'

        OlivOSAIChatAssassin.logger.log(f'[Skills] 注入技能片段: {len(result)} 字符')
        return result

    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'[Skills] 检索失败: {e}')
        return ''
