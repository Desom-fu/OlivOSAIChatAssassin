"""Codex skill discovery, routing, and progressive content retrieval.

The legacy manager mixed routing, reference search, fuzzy matching, and content
slicing in one scoring loop.  This implementation keeps those stages separate:

1. Index only ``SKILL.md`` routing metadata at startup.
2. Select a skill from its name, description, headings, and navigation tables.
3. Search referenced Markdown lazily, after selection (or as a fallback for an
   exact domain term that is absent from the routing metadata).
4. Retrieve compact, query-centred sections within a strict character budget.

The public functions intentionally match the old module so the plugin and the
existing regression scripts can migrate without changing their call sites.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

import OlivOSAIChatAssassin


_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$', re.MULTILINE)
_MEDIA_TAG_RE = re.compile(r'\[(?:OP|CQ):[^\]]+\]', re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+\.md)(?:#[^)]+)?\)', re.IGNORECASE)
_CODE_PATH_RE = re.compile(r'[`"\']([^`"\']+\.md)(?:#[^`"\']+)?[`"\']', re.IGNORECASE)
_PLAIN_PATH_RE = re.compile(r'(?<![\w.-])((?:references?|assets)/[^\s<>|]+\.md)', re.IGNORECASE)
_CJK_RE = re.compile(r'[\u3400-\u9fff]{2,40}')
_EN_RE = re.compile(r'[a-z][a-z0-9]*(?:[._+-][a-z0-9]+)*', re.IGNORECASE)

_CONTEXT_MESSAGES_DEFAULT = 12
_CONTEXT_MESSAGES_MAX = 64
_MAX_TERMS = 64
_SMALL_SKILL_CHARS = 1400
_MIN_ROUTE_SCORE = 10.0
_SECONDARY_SCORE_RATIO = 0.75

_CJK_STOP_WORDS = frozenset({
    '一个', '一些', '一下', '一样', '不是', '不过', '为什么', '什么', '他们', '你们', '的话',
    '但是', '可以', '哪些', '哪个', '如何', '如果', '它们', '就是', '应该', '怎么', '怎样',
    '我们', '所以', '时候', '是否', '有没有', '这个', '这些', '那个', '那些', '里面', '需要',
})
_EN_STOP_WORDS = frozenset({
    'about', 'after', 'also', 'and', 'are', 'can', 'could', 'create', 'does', 'for', 'from', 'have',
    'even', 'help', 'how', 'into', 'make', 'more', 'need', 'please', 'should', 'tell', 'that', 'the', 'their',
    'this', 'use', 'using', 'want', 'what', 'when', 'where', 'which', 'who', 'why', 'with', 'would',
    'write', 'your', 'two',
})
_QUERY_NOISE_PARTS = frozenset({
    '的', '里面', '什么', '怎么', '如何', '哪些', '哪个', '是否', '能不能', '可以', '应该', '小芙',
})
_TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    '個': '个', '兩': '两', '來': '来', '們': '们', '係': '系', '寫': '写', '創': '创', '則': '则',
    '劃': '划', '動': '动', '參': '参', '發': '发', '問': '问', '國': '国', '場': '场', '夠': '够',
    '學': '学', '實': '实', '對': '对', '將': '将', '應': '应', '開': '开', '後': '后', '從': '从',
    '復': '复', '戰': '战', '擇': '择', '擊': '击', '據': '据', '數': '数', '斷': '断', '於': '于',
    '無': '无', '時': '时', '書': '书', '會': '会', '樣': '样', '標': '标', '檔': '档', '權': '权',
    '氣': '气', '沒': '没', '為': '为', '爲': '为', '當': '当', '畫': '画', '碼': '码', '稱': '称',
    '種': '种', '簡': '简', '籤': '签', '組': '组', '維': '维', '網': '网', '義': '义', '舉': '举',
    '與': '与', '術': '术', '號': '号', '虛': '虚', '裡': '里', '製': '制', '規': '规', '計': '计',
    '註': '注', '設': '设', '說': '说', '課': '课', '請': '请', '讀': '读', '譯': '译', '護': '护',
    '讓': '让', '資': '资', '質': '质', '輸': '输', '轉': '转', '這': '这', '還': '还', '過': '过',
    '邊': '边', '邏': '逻', '關': '关', '靈': '灵', '體': '体', '點': '点', '麼': '么', '嗎': '吗',
    '誰': '谁', '負': '负', '該': '该', '題': '题', '報': '报', '類': '类', '吳': '吴',
})

_cache_lock = threading.RLock()
_file_cache: dict[str, tuple[int, int, str]] = {}
_context_cache: dict[str, str] = {}
_index_revision = 0


def _log(message: str) -> None:
    try:
        OlivOSAIChatAssassin.logger.log(message)
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        OlivOSAIChatAssassin.logger.warn(message)
    except Exception:
        pass


def _normalize(text: Any) -> str:
    if not isinstance(text, str):
        return ''
    value = _MEDIA_TAG_RE.sub(' ', text).lower().translate(_TRADITIONAL_TO_SIMPLIFIED)
    value = value.replace('app json', 'app.json').replace('life cycle', 'lifecycle')
    value = re.sub(r'[/\\_]+', ' ', value)
    value = re.sub(r'[^a-z0-9.#+\-\u3400-\u9fff]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _read_text(path: str) -> str | None:
    try:
        stat = os.stat(path)
        cache_key = os.path.abspath(path)
        signature = (stat.st_mtime_ns, stat.st_size)
        with _cache_lock:
            cached = _file_cache.get(cache_key)
            if cached and cached[:2] == signature:
                return cached[2]
        with open(path, encoding='utf-8-sig') as file_obj:
            content = file_obj.read()
        with _cache_lock:
            _file_cache[cache_key] = (signature[0], signature[1], content)
        return content
    except (OSError, UnicodeError) as error:
        _warn(f'[Skills V2] 读取失败 [{path}]: {error}')
        return None


def clear_cache() -> None:
    """Clear file and query caches without discarding the current index."""
    with _cache_lock:
        _file_cache.clear()
        _context_cache.clear()


def _frontmatter_and_body(content: str) -> tuple[dict[str, str], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, content
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == '---'), -1)
    if end < 0:
        return {}, content

    values: dict[str, str] = {}
    index = 1
    while index < end:
        match = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', lines[index])
        if not match:
            index += 1
            continue
        key, raw_value = match.group(1), match.group(2).strip()
        if raw_value in {'|', '>', '|-', '>-'}:
            folded = raw_value.startswith('>')
            parts = []
            index += 1
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                parts.append(lines[index].strip())
                index += 1
            values[key] = (' ' if folded else '\n').join(parts).strip()
            continue
        values[key] = raw_value.strip('"\'')
        index += 1
    return values, '\n'.join(lines[end + 1 :]).lstrip()


def _parse_headers(content: str) -> list[dict[str, Any]]:
    headers = []
    for match in _HEADING_RE.finditer(content):
        headers.append({
            'title': re.sub(r'\s+\{#[^}]+\}\s*$', '', match.group(2)).strip(),
            'line': content.count('\n', 0, match.start()) + 1,
            'level': len(match.group(1)),
        })
    return [header for header in headers if header['level'] in (2, 3)]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', cell.replace(' ', '')) for cell in cells)


def _parse_nav_tables(content: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    lines = content.splitlines()
    index = 0
    while index + 1 < len(lines):
        if '|' not in lines[index] or '|' not in lines[index + 1]:
            index += 1
            continue
        header = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
        separator = [cell.strip() for cell in lines[index + 1].strip().strip('|').split('|')]
        if len(header) < 2 or len(header) != len(separator) or not _is_separator_row(separator):
            index += 1
            continue
        index += 2
        while index < len(lines) and '|' in lines[index]:
            cells = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
            if len(cells) == len(header):
                result.append(dict(zip(header, cells)))
            index += 1
    return result


def _extract_references(content: str, skill_dir: str) -> list[str]:
    candidates = []
    for pattern in (_MARKDOWN_LINK_RE, _CODE_PATH_RE, _PLAIN_PATH_RE):
        candidates.extend(match.group(1) for match in pattern.finditer(content))

    root = Path(skill_dir).resolve()
    references = []
    seen = set()
    for candidate in candidates:
        relative = candidate.strip().strip('<>').replace('/', os.sep)
        try:
            resolved = (root / relative).resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or resolved.suffix.lower() != '.md':
            continue
        relative_posix = resolved.relative_to(root).as_posix()
        if relative_posix not in seen:
            seen.add(relative_posix)
            references.append(relative_posix)
    return references


def _parse_sections(content: str, source: str) -> list[dict[str, Any]]:
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        return [{'title': Path(source).stem, 'level': 1, 'text': content.strip(), 'source': source}]
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        title = re.sub(r'\s+\{#[^}]+\}\s*$', '', match.group(2)).strip()
        sections.append({
            'title': title,
            'level': len(match.group(1)),
            'text': content[match.start() : end].strip(),
            'source': source,
        })
    return sections


def _parse_skill(skill_md_path: str) -> dict[str, Any] | None:
    content = _read_text(skill_md_path)
    if content is None:
        return None
    metadata, body = _frontmatter_and_body(content)
    name = metadata.get('name', '').strip()
    if not name:
        _warn(f'[Skills V2] SKILL.md 缺少 name [{skill_md_path}]')
        return None
    skill_dir = os.path.dirname(skill_md_path)
    headers = _parse_headers(body)
    nav_table = _parse_nav_tables(body)
    nav_questions = ' '.join(next(iter(row.values()), '') for row in nav_table)
    nav_targets = ' '.join(' '.join(list(row.values())[1:]) for row in nav_table)
    return {
        'name': name,
        'description': metadata.get('description', '').strip(),
        'desc_text': f'{name} {metadata.get("description", "")}'.strip(),
        'dir': skill_dir,
        'skill_md': skill_md_path,
        'headers': headers,
        'nav_table': nav_table,
        'references': _extract_references(body, skill_dir),
        '_body': body,
        '_sections': _parse_sections(body, 'SKILL.md'),
        '_name_norm': _normalize(name),
        '_description_norm': _normalize(metadata.get('description', '')),
        '_headings_norm': _normalize(' '.join(header['title'] for header in headers)),
        '_nav_questions_norm': _normalize(nav_questions),
        '_nav_targets_norm': _normalize(nav_targets),
        '_body_norm': _normalize(body),
    }


def _configured_skill_dirs() -> list[str]:
    raw_dirs = []
    primary = getattr(OlivOSAIChatAssassin.data, 'gSkillsDir', '')
    if isinstance(primary, str) and primary.strip():
        raw_dirs.append(primary)
    extras = getattr(OlivOSAIChatAssassin.data, 'gSkillsExtraDirs', [])
    if isinstance(extras, (list, tuple)):
        raw_dirs.extend(path for path in extras if isinstance(path, str) and path.strip())
    if not raw_dirs:
        raw_dirs.append('~/.codex/skills')

    result = []
    seen = set()
    for raw_path in raw_dirs:
        path = os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path))))
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def build_skills_index() -> dict[str, dict[str, Any]]:
    """Recursively discover valid SKILL.md files in configured roots."""
    global _index_revision

    index: dict[str, dict[str, Any]] = {}
    clear_cache()
    for skills_dir in _configured_skill_dirs():
        if not os.path.isdir(skills_dir):
            _warn(f'[Skills V2] 技能目录不存在 [{skills_dir}]')
            continue
        try:
            for root, directories, files in os.walk(skills_dir):
                directories.sort()
                if 'SKILL.md' not in files:
                    continue
                entry = _parse_skill(os.path.join(root, 'SKILL.md'))
                if entry is None:
                    continue
                name = entry['name']
                if name in index:
                    _warn(f'[Skills V2] 跳过重复技能 [{name}] [{entry["skill_md"]}]')
                    continue
                index[name] = entry
                _log(f'[Skills V2] 已索引 {name}')
        except OSError as error:
            _warn(f'[Skills V2] 扫描失败 [{skills_dir}]: {error}')
    with _cache_lock:
        _index_revision += 1
        _context_cache.clear()
    _log(f'[Skills V2] 索引完成，共 {len(index)} 个技能')
    return index


def _term_specificity(term: str) -> float:
    if _CJK_RE.fullmatch(term):
        return min(2.4, 0.65 + len(term) * 0.35)
    return min(2.2, 0.55 + len(term) / 7)


def _extract_terms_from_text(text: str) -> list[str]:
    normalized = _normalize(text)
    candidates: dict[str, tuple[int, int]] = {}
    order = 0
    for segment in _CJK_RE.findall(normalized):
        maximum = min(6, len(segment))
        for size in range(maximum, 1, -1):
            for start in range(len(segment) - size + 1):
                term = segment[start : start + size]
                if term in _CJK_STOP_WORDS:
                    continue
                frequency, first = candidates.get(term, (0, order))
                candidates[term] = (frequency + 1, first)
                order += 1
    english_words = [word for word in _EN_RE.findall(normalized) if word not in _EN_STOP_WORDS and len(word) >= 3]
    for word in english_words:
        frequency, first = candidates.get(word, (0, order))
        candidates[word] = (frequency + 1, first)
        order += 1
    for size in (3, 2):
        for start in range(len(english_words) - size + 1):
            phrase = ' '.join(english_words[start : start + size])
            frequency, first = candidates.get(phrase, (0, order))
            candidates[phrase] = (frequency + 1, first)
            order += 1
    ranked = sorted(
        candidates.items(),
        key=lambda item: (-(item[1][0] * _term_specificity(item[0])), item[1][1]),
    )
    terms = [term for term, _ in ranked[:_MAX_TERMS]]
    concept_terms = []

    def add_concepts(*items: str) -> None:
        for item in items:
            normalized_item = _normalize(item)
            if normalized_item and normalized_item not in concept_terms:
                concept_terms.append(normalized_item)

    if 'spiritual ability' in normalized:
        add_concepts('灵能力')
    if 'negative tag' in normalized:
        add_concepts('负面标签', '缺陷标签', '灵能力标签')
    if 'action check' in normalized:
        add_concepts('行动检定')
    if 'soul weapon' in normalized:
        add_concepts('灵魂武器')
    if 'wu yan' in normalized:
        add_concepts('吴焰')
    if '上报' in normalized and '事件' in normalized:
        add_concepts('framework events', 'lifecycle events', '事件上报')
    if re.search(r'(?:选择)?\d+个?目标', normalized) or ('多选' in normalized and '灵能力' in normalized):
        add_concepts('多个目标', '选择目标')
    return (concept_terms + terms)[:_MAX_TERMS]


def extract_context_keywords(
    history: list[dict[str, Any]],
    context_messages: int = _CONTEXT_MESSAGES_DEFAULT,
) -> list[str]:
    """Extract deterministic CJK n-grams and English terms from recent messages."""
    if not isinstance(history, list):
        return []
    try:
        window_size = min(_CONTEXT_MESSAGES_MAX, max(1, int(context_messages)))
    except (TypeError, ValueError):
        window_size = _CONTEXT_MESSAGES_DEFAULT
    recent = history[-window_size:]
    ordered_terms = []
    seen = set()
    for item in reversed(recent):
        message = item.get('message', '') if isinstance(item, dict) else ''
        if not isinstance(message, str) or not _normalize(message):
            continue
        for term in _extract_terms_from_text(message):
            if term not in seen:
                seen.add(term)
                ordered_terms.append(term)
                if len(ordered_terms) >= _MAX_TERMS:
                    return ordered_terms
    return ordered_terms


def _latest_message_terms(history: list[dict[str, Any]]) -> list[str]:
    for item in reversed(history or []):
        message = item.get('message', '') if isinstance(item, dict) else ''
        if isinstance(message, str) and _normalize(message):
            return _extract_terms_from_text(message)
    return []


def _content_message_terms(history: list[dict[str, Any]], max_lookback: int = 6) -> list[str]:
    """Join short, fragmented messages from the latest sender for content retrieval."""
    latest_index = -1
    latest_item: dict[str, Any] = {}
    for index in range(len(history or []) - 1, -1, -1):
        item = history[index]
        if isinstance(item, dict) and _normalize(item.get('message', '')):
            latest_index = index
            latest_item = item
            break
    if latest_index < 0:
        return []

    latest_message = latest_item.get('message', '')
    latest_normalized = _normalize(latest_message)
    should_join = bool(re.search(r'(?:多少|几个|几点|怎么|如何|^要$|^mp$|[?？])', latest_normalized))
    if not should_join:
        return _extract_terms_from_text(latest_message)

    sender_key = latest_item.get('user_id') or latest_item.get('nickname')
    messages = [latest_message]
    lower_bound = max(0, latest_index - max_lookback)
    for index in range(latest_index - 1, lower_bound - 1, -1):
        item = history[index]
        if not isinstance(item, dict):
            continue
        item_sender = item.get('user_id') or item.get('nickname')
        if sender_key and item_sender != sender_key:
            continue
        message = item.get('message', '')
        if not isinstance(message, str) or not _normalize(message):
            continue
        messages.append(message)
        combined_length = len(_normalize(' '.join(reversed(messages))).replace(' ', ''))
        if combined_length >= 24:
            break
    return _extract_terms_from_text(' '.join(reversed(messages)))


def _field_score(terms: list[str], text: str, weight: float, cap: float) -> tuple[float, list[str]]:
    if not text:
        return 0.0, []
    hits = []
    score = 0.0
    for term in terms:
        if term in text:
            hits.append(term)
            score += weight * _term_specificity(term)
            if score >= cap:
                return cap, hits
    return score, hits


def _matched_headers(entry: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    return [
        header
        for header in entry.get('headers', [])
        if any(term in _normalize(header.get('title', '')) for term in terms)
    ]


def _matched_nav(entry: dict[str, Any], terms: list[str]) -> list[dict[str, str]]:
    matches = []
    for row in entry.get('nav_table', []):
        question = _normalize(next(iter(row.values()), ''))
        route_terms = [
            term
            for term in terms
            if (not _CJK_RE.fullmatch(term) and len(term) >= 5)
            or (_CJK_RE.fullmatch(term) and len(term) >= 4)
        ]
        if any(term in question or question in term for term in route_terms if question):
            matches.append(row)
    return matches


def _route_skill(entry: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    name_score, name_hits = _field_score(terms, entry['_name_norm'], 10.0, 60.0)
    description_score, description_hits = _field_score(terms, entry['_description_norm'], 5.0, 80.0)
    heading_score, heading_hits = _field_score(terms, entry['_headings_norm'], 4.0, 45.0)
    nav_score, nav_hits = _field_score(terms, entry['_nav_questions_norm'], 6.0, 60.0)
    body_score, body_hits = _field_score(terms, entry['_body_norm'], 0.5, 4.0)
    matched_nav = _matched_nav(entry, terms)
    nav_score = max(nav_score, min(60.0, len(matched_nav) * 12.0))
    score = name_score + description_score + heading_score + nav_score + body_score
    return {
        'entry': entry,
        'score': round(score, 3),
        'route_score': round(score, 3),
        'matched_headers': _matched_headers(entry, terms),
        'matched_nav': matched_nav,
        'matched_body_keywords': body_hits,
        'matched_reference_keywords': [],
        'keywords': terms,
        'evidence': {
            'name': name_hits,
            'description': description_hits,
            'headings': heading_hits,
            'navigation': nav_hits,
            'body': body_hits,
            'references': [],
        },
    }


def _reference_fallback(entry: dict[str, Any], terms: list[str]) -> tuple[float, list[str]]:
    specific_terms = [
        term for term in terms
        if (_CJK_RE.fullmatch(term) and len(term) >= 2) or (not _CJK_RE.fullmatch(term) and len(term) >= 4)
    ]
    if not specific_terms:
        return 0.0, []
    hits = []
    for relative_path in entry.get('references', []):
        content = _read_text(os.path.join(entry['dir'], relative_path))
        normalized = _normalize(content or '')
        for term in specific_terms:
            if term in normalized and term not in hits:
                hits.append(term)
    if not hits:
        return 0.0, []
    longest = max(hits, key=lambda term: (_term_specificity(term), len(term)))
    confidence = 8.0 * _term_specificity(longest) + min(8.0, len(hits) * 0.6)
    return round(confidence, 3), hits


def _get_config(bot_hash: str) -> dict[str, Any]:
    try:
        config = OlivOSAIChatAssassin.data.gData.getConfig(bot_hash)
        return config if isinstance(config, dict) else {}
    except Exception:
        return {}


def _config_value(config: dict[str, Any], key: str, fallback: Any) -> Any:
    defaults = getattr(OlivOSAIChatAssassin.data, 'configDefault', {})
    return config.get(key, defaults.get(key, fallback))


def _context_message_count(config: dict[str, Any]) -> int:
    try:
        value = int(_config_value(config, 'history_size', _CONTEXT_MESSAGES_DEFAULT))
    except (TypeError, ValueError):
        value = _CONTEXT_MESSAGES_DEFAULT
    return min(_CONTEXT_MESSAGES_MAX, max(1, value))


def match_skills(
    keywords: list[str],
    bot_hash: str,
    content_keywords: list[str] | None = None,
    allow_reference_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Route terms to skills, with lazy reference fallback when routing is inconclusive."""
    index = getattr(OlivOSAIChatAssassin.data, 'gSkillsIndex', {})
    terms = []
    seen = set()
    for keyword in keywords or []:
        normalized = _normalize(keyword)
        if normalized and normalized not in seen:
            seen.add(normalized)
            terms.append(normalized)
    if not isinstance(index, dict) or not index or not terms:
        return []

    config = _get_config(bot_hash)
    try:
        max_matches = max(1, int(_config_value(config, 'skills_max_matches', 2)))
    except (TypeError, ValueError):
        max_matches = 2

    routed = [_route_skill(entry, terms) for entry in index.values()]
    content_terms = []
    for keyword in content_keywords or terms:
        normalized = _normalize(keyword)
        if normalized and normalized not in content_terms:
            content_terms.append(normalized)
    for result in routed:
        result['keywords'] = content_terms
    confident = [result for result in routed if result['route_score'] >= _MIN_ROUTE_SCORE]

    if not confident and allow_reference_fallback:
        for result in routed:
            reference_score, reference_hits = _reference_fallback(result['entry'], terms)
            if reference_score < _MIN_ROUTE_SCORE:
                continue
            result['score'] = reference_score
            result['matched_reference_keywords'] = reference_hits
            result['evidence']['references'] = reference_hits
            confident.append(result)

    confident.sort(key=lambda result: (-result['score'], result['entry']['name']))
    if not confident:
        return []
    threshold = max(_MIN_ROUTE_SCORE, confident[0]['score'] * _SECONDARY_SCORE_RATIO)
    return [result for result in confident if result['score'] >= threshold][:max_matches]


def _looks_like_skill_request(history: list[dict[str, Any]]) -> bool:
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        message = item.get('message', '')
        normalized = _normalize(message)
        if not normalized:
            continue
        if re.search(r'[?？]|(?:什么|怎么|如何|多少|几个|几点|哪段|哪一段|谁|吗|么)$', message):
            return True
        if '小芙' in normalized and re.search(r'(?:翻|看|读|找|说|说明|告诉|回答|算|闭嘴)', normalized):
            return True
        return False
    return False


def _select_matches_for_history(
    history: list[dict[str, Any]],
    bot_hash: str,
    context_messages: int,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    context_keywords = extract_context_keywords(history, context_messages=context_messages)
    content_keywords = _content_message_terms(history)
    request_like = _looks_like_skill_request(history)
    matches = match_skills(
        content_keywords,
        bot_hash,
        content_keywords=content_keywords,
        allow_reference_fallback=request_like,
    )
    if not matches and request_like:
        matches = match_skills(
            context_keywords,
            bot_hash,
            content_keywords=content_keywords,
            allow_reference_fallback=False,
        )
    return context_keywords, content_keywords, matches


def _reference_sections(entry: dict[str, Any]) -> list[dict[str, Any]]:
    sections = []
    for relative_path in entry.get('references', []):
        content = _read_text(os.path.join(entry['dir'], relative_path))
        if content:
            sections.extend(_parse_sections(content, relative_path))
    return sections


def _reference_query_windows(entry: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    """Build line-centred windows for concrete query terms absent from routing metadata."""
    route_text = ' '.join((
        entry.get('_name_norm', ''),
        entry.get('_description_norm', ''),
        entry.get('_headings_norm', ''),
        entry.get('_nav_questions_norm', ''),
    ))
    concrete_terms = [
        term
        for term in terms
        if term not in route_text
        and not any(part in term for part in _QUERY_NOISE_PARTS)
        and ((_CJK_RE.fullmatch(term) and len(term) >= 2) or len(term) >= 4)
    ]
    concrete_terms.sort(key=lambda term: (-len(term), terms.index(term)))

    windows = []
    seen = set()
    for relative_path in entry.get('references', []):
        content = _read_text(os.path.join(entry['dir'], relative_path))
        if not content:
            continue
        lines = content.splitlines()
        normalized_lines = [_normalize(line) for line in lines]
        for term in concrete_terms:
            hit_index = next((index for index, line in enumerate(normalized_lines) if term in line), -1)
            if hit_index < 0:
                continue
            heading_index = hit_index
            while heading_index >= 0 and not re.match(r'^#{1,6}\s+', lines[heading_index].strip()):
                heading_index -= 1
            title = (
                re.sub(r'^#{1,6}\s+', '', lines[heading_index].strip())
                if heading_index >= 0
                else Path(relative_path).stem
            )
            identity = (relative_path, title, term)
            if identity in seen:
                continue
            seen.add(identity)
            start = max(heading_index if heading_index >= 0 else hit_index - 5, hit_index - 8)
            end = min(len(lines), hit_index + 12)
            windows.append({
                'title': title,
                'level': 2,
                'text': '\n'.join(lines[start:end]).strip(),
                'source': relative_path,
                '_query_term': term,
                '_query_priority': 200.0 + len(term) * 8.0,
            })
            if len(windows) >= 6:
                return windows
    return windows


def _navigation_targets(match_result: dict[str, Any]) -> list[str]:
    targets = []
    for row in match_result.get('matched_nav', []):
        values = list(row.values())[1:]
        targets.extend(_normalize(value) for value in values if value)
    return targets


def _score_section(
    section: dict[str, Any],
    terms: list[str],
    nav_targets: list[str],
    term_frequency: dict[str, int],
    section_count: int,
) -> float:
    title = _normalize(section.get('title', ''))
    body = _normalize(section.get('text', ''))
    score = 0.0
    for target in nav_targets:
        if target and (target in title or title in target):
            score += 45.0
    for term in terms:
        specificity = _term_specificity(term)
        document_frequency = term_frequency.get(term, section_count)
        rarity = 0.2 + math.log((section_count + 1) / (document_frequency + 1))
        if term in title or (title and title in term):
            score += 12.0 * specificity * rarity
        occurrences = body.count(term)
        if occurrences:
            score += min(4, occurrences) * 1.8 * specificity * rarity
    if section.get('source') == 'SKILL.md':
        score += 0.25
    return score


def _best_excerpt(section: dict[str, Any], terms: list[str], budget: int) -> str:
    text = section.get('text', '').strip()
    if budget <= 0 or not text:
        return ''
    if len(text) <= budget:
        return text

    normalized = _normalize(text)
    best_term = next((term for term in terms if term in normalized), '')
    raw_position = -1
    if best_term:
        raw_position = text.lower().translate(_TRADITIONAL_TO_SIMPLIFIED).find(best_term)
    if raw_position < 0:
        raw_position = 0

    heading = f'## {section.get("title", "相关内容")}\n'
    body_budget = max(0, budget - len(heading) - 10)
    start = max(0, raw_position - body_budget // 3)
    end = min(len(text), start + body_budget)
    start = max(0, end - body_budget)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = '...\n' + excerpt
    if end < len(text):
        excerpt += '\n[...]'
    result = heading + excerpt
    return result[:budget]


def slice_skill_content(match_result: dict[str, Any], budget_chars: int) -> str:
    """Retrieve the highest scoring skill/reference sections within ``budget_chars``."""
    try:
        budget = max(0, int(budget_chars))
    except (TypeError, ValueError):
        return ''
    entry = match_result.get('entry', {})
    name = entry.get('name', 'unknown')
    description = entry.get('description', '')
    header = f'[Skill: {name}]'
    if description:
        header += '\n' + description[:240]
    if budget <= len(header):
        return header[:budget]

    body = entry.get('_body')
    if body is None:
        content = _read_text(entry.get('skill_md', '')) or ''
        _, body = _frontmatter_and_body(content)
    if len(body) <= _SMALL_SKILL_CHARS and len(header) + 1 + len(body) <= budget:
        return header + '\n' + body.strip()

    terms = match_result.get('keywords', [])
    nav_targets = _navigation_targets(match_result)
    query_windows = _reference_query_windows(entry, terms)
    sections = query_windows + list(entry.get('_sections', [])) + _reference_sections(entry)
    normalized_sections = [_normalize(section.get('text', '')) for section in sections]
    term_frequency = {
        term: sum(1 for text in normalized_sections if term in text)
        for term in terms
    }

    requested_titles = {
        _normalize(item.get('title', '')) for item in match_result.get('matched_headers', [])
    }
    ranked = []
    for section in sections:
        score = _score_section(section, terms, nav_targets, term_frequency, len(sections))
        score += section.get('_query_priority', 0.0)
        if _normalize(section.get('title', '')) in requested_titles:
            score += 100.0
        if score > 0:
            ranked.append((score, section))
    ranked.sort(key=lambda item: (-item[0], item[1].get('source', ''), item[1].get('title', '')))
    if not ranked:
        ranked = [(0.0, section) for section in entry.get('_sections', [])[:1]]

    remaining = budget - len(header) - 1
    chunks = []
    seen = set()
    for _, section in ranked:
        identity = (section.get('source'), section.get('title'))
        if identity in seen or remaining < 80:
            continue
        seen.add(identity)
        slots_left = max(1, min(3, len(ranked) - len(chunks)))
        section_budget = remaining if slots_left == 1 else max(160, remaining // slots_left)
        section_budget = min(remaining, section_budget)
        chunk = _best_excerpt(section, terms, section_budget)
        if not chunk:
            continue
        chunks.append(chunk)
        remaining -= len(chunk) + 2
        if len(chunks) >= 3:
            break
    result = header + ('\n' + '\n\n'.join(chunks) if chunks else '')
    return result[:budget]


def get_skills_context(history: list[dict[str, Any]], bot_hash: str) -> str:
    """Select relevant skills and return prompt-ready content under a strict budget."""
    try:
        config = _get_config(bot_hash)
        if not bool(_config_value(config, 'skills_enable', True)):
            return ''
        index = getattr(OlivOSAIChatAssassin.data, 'gSkillsIndex', {})
        if not index:
            return ''
        try:
            max_chars = max(0, int(_config_value(config, 'skills_max_chars', 2000)))
        except (TypeError, ValueError):
            max_chars = 2000
        if max_chars < 80:
            return ''

        context_messages = _context_message_count(config)
        recent_messages = [
            item.get('message', '') for item in history[-context_messages:] if isinstance(item, dict)
        ]
        keywords, content_keywords, matches = _select_matches_for_history(
            history,
            bot_hash,
            context_messages,
        )
        if not keywords:
            return ''
        _log(f'[Skills V2] 上下文窗口: {len(recent_messages)}/{context_messages} 条')
        _log(f'[Skills V2] 上下文关键词: {keywords[:10]}')
        _log(f'[Skills V2] 当前消息关键词: {content_keywords[:10]}')

        if not matches:
            return ''
        _log(
            '[Skills V2] 匹配到 '
            f'{len(matches)} 个技能: '
            f'{[(match["entry"]["name"], match["score"]) for match in matches]}'
        )

        cache_source = repr((_index_revision, bot_hash, max_chars, context_messages, recent_messages))
        cache_key = hashlib.sha256(cache_source.encode('utf-8')).hexdigest()
        with _cache_lock:
            cached = _context_cache.get(cache_key)
        if cached is not None:
            _log(f'[Skills V2] 注入技能片段: {len(cached)} 字符（缓存）')
            return cached

        prefix = '\n# 技能知识\n'
        remaining = max_chars - len(prefix)
        chunks = []
        total_score = sum(max(1.0, match['score']) for match in matches)
        for index_in_matches, match in enumerate(matches):
            matches_left = len(matches) - index_in_matches
            proportional = int(remaining * max(1.0, match['score']) / total_score)
            minimum_for_rest = max(0, (matches_left - 1) * 180)
            budget = min(remaining - minimum_for_rest, max(180, proportional))
            chunk = slice_skill_content(match, budget)
            if chunk:
                chunks.append(chunk)
                remaining -= len(chunk) + 2
            total_score -= max(1.0, match['score'])
        result = (prefix + '\n\n'.join(chunks))[:max_chars] if chunks else ''
        if result:
            _log(f'[Skills V2] 注入技能片段: {len(result)} 字符')
        with _cache_lock:
            _context_cache[cache_key] = result
        return result
    except Exception as error:
        _warn(f'[Skills V2] 检索失败: {error}')
        return ''


def explain_skill_selection(history: list[dict[str, Any]], bot_hash: str) -> list[dict[str, Any]]:
    """Return a compact, test/report friendly explanation of routing decisions."""
    config = _get_config(bot_hash)
    context_messages = _context_message_count(config)
    _, _, matches = _select_matches_for_history(history, bot_hash, context_messages)
    explanations = []
    for match in matches:
        explanations.append({
            'skill': match['entry']['name'],
            'score': match['score'],
            'evidence': {key: value for key, value in match['evidence'].items() if value},
        })
    return explanations
