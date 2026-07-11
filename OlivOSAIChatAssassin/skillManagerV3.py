"""通用 Skill 检索器：标准历史上下文、数据驱动路由、可追溯段落召回。"""

from __future__ import annotations

import os
import re
import json
import time
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import jieba
import yaml
from rank_bm25 import BM25Okapi
import translators

import OlivOSAIChatAssassin

_REF_RE = re.compile(r'`?((?:references?|assets)/[^`\s)]+\.(?:md|txt))`?', re.I)
_HEAD_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$', re.M)
_LATIN_RE = re.compile(r'[a-z][a-z0-9_.+/-]*', re.I)
_CJK_RE = re.compile(r'[\u3400-\u9fff]')
_SPACE_RE = re.compile(r'\s+')
_query_cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_translation_cache: dict[str, str] = {}


def clear_cache() -> None:
    """索引由 load_skills 统一重建；保留兼容接口。"""
    _query_cache.clear()


def _log(message: str) -> None:
    try:
        OlivOSAIChatAssassin.logger.log(message)
    except Exception:
        pass


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return ''


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n?', text, re.S)
    if not match:
        return {}, text
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        metadata = {}
    return metadata if isinstance(metadata, dict) else {}, text[match.end():]


def _tokens(text: str) -> list[str]:
    """使用 jieba 搜索模式分词；英文保持技术标识符原貌。"""
    normalized = _SPACE_RE.sub(' ', str(text or '').lower()).strip()
    tokens = [token.strip() for token in jieba.cut_for_search(normalized) if token.strip()]
    tokens.extend(_LATIN_RE.findall(normalized))
    base = [token for token in tokens if len(token) > 1 or _CJK_RE.search(token)]
    # 字符 n-gram 只在单个连续中文片段内部生成，绝不跨消息或标点拼接。
    shingles = []
    for run in re.findall(r'[\u3400-\u9fff]+', normalized):
        shingles.extend(
            run[index:index + size]
            for size in range(2, min(6, len(run)) + 1)
            for index in range(len(run) - size + 1)
        )
    return list(dict.fromkeys(base + shingles))


def _metadata_terms(metadata: dict[str, Any]) -> str:
    """读取 Skill 作者声明的可选离线检索词，不内置任何领域词表。"""
    values = []
    for key in ('aliases', 'keywords', 'triggers'):
        value = metadata.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return ' '.join(values)


def _translate_foreign_query(text: str) -> str:
    if _CJK_RE.search(text) or not _LATIN_RE.search(text):
        return ''
    cache_path = Path(OlivOSAIChatAssassin.data.gSkillsTranslationCachePath)
    if not _translation_cache and cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                _translation_cache.update(data)
        except (OSError, ValueError, TypeError):
            pass
    if text in _translation_cache:
        return _translation_cache[text]
    result_box: list[str] = []

    def translate_worker() -> None:
        try:
            result_box.append(translators.translate_text(
                text,
                translator=OlivOSAIChatAssassin.data.gSkillsTranslationBackend,
                from_language=OlivOSAIChatAssassin.data.gSkillsTranslationFromLanguage,
                to_language=OlivOSAIChatAssassin.data.gSkillsTranslationToLanguage,
                timeout=OlivOSAIChatAssassin.data.gSkillsTranslationTimeout,
            ) or '')
        except Exception:
            result_box.append('')

    worker = threading.Thread(target=translate_worker, daemon=True)
    worker.start()
    worker.join(max(0.1, float(OlivOSAIChatAssassin.data.gSkillsTranslationTimeout)))
    translated = result_box[0] if result_box else ''
    _translation_cache[text] = translated
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(_translation_cache, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except OSError:
        pass
    return translated


def _sections(path: str, text: str, skill_name: str) -> list[dict[str, Any]]:
    headings = list(_HEAD_RE.finditer(text))
    if not headings:
        return [{'skill': skill_name, 'source': path, 'heading': Path(path).name, 'text': text.strip()}]
    result = []
    prefix = text[:headings[0].start()].strip()
    if prefix:
        result.append({'skill': skill_name, 'source': path, 'heading': Path(path).name, 'text': prefix})
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        content = text[heading.start():end].strip()
        if content:
            result.append({
                'skill': skill_name,
                'source': path,
                'heading': heading.group(2).strip(),
                'heading_markup': heading.group(1),
                'text': content,
            })
    return result


def _skill_dirs() -> list[str]:
    roots = [getattr(
        OlivOSAIChatAssassin.data,
        'gSkillsDir',
        './plugin/data/OlivOSAIChatAssassin/skills',
    )]
    roots.extend(getattr(OlivOSAIChatAssassin.data, 'gSkillsExtraDirs', []) or [])
    return [os.path.abspath(os.path.expanduser(str(root))) for root in roots]


def build_skills_index() -> dict[str, dict[str, Any]]:
    result = {}
    for skills_dir in _skill_dirs():
        if not os.path.isdir(skills_dir):
            continue
        for root, directories, files in os.walk(skills_dir):
            directories.sort()
            if 'SKILL.md' not in files:
                continue
            skill_path = os.path.join(root, 'SKILL.md')
            raw = _read(skill_path)
            metadata, body = _frontmatter(raw)
            name = str(metadata.get('name') or os.path.basename(root))
            if name in result:
                continue
            sources = [skill_path]
            for relative_path in _REF_RE.findall(body):
                candidate = os.path.abspath(os.path.join(root, relative_path.replace('/', os.sep)))
                if os.path.isfile(candidate) and candidate not in sources:
                    sources.append(candidate)
            chunks = [chunk for source in sources for chunk in _sections(source, _read(source), name)]
            description = str(metadata.get('description') or '')
            headings = ' '.join(chunk['heading'] for chunk in chunks)
            # 路由文档完全由 skill 自身资料生成，不包含任何领域专用映射。
            metadata_terms = _metadata_terms(metadata)
            route_text = f'{name} {description} {metadata_terms} {headings} {body}'
            result[name] = {
                'name': name,
                'description': description,
                'metadata_terms': metadata_terms,
                'skill_md': skill_path,
                'sources': sources,
                'chunks': chunks,
                'route_text': route_text,
                'route_tokens': _tokens(route_text),
                'foreign_route_tokens': _tokens(f'{name} {description} {metadata_terms}'),
            }
    entries = list(result.values())
    route_documents = [entry['route_tokens'] for entry in entries]
    route_bm25 = BM25Okapi(route_documents) if entries and any(route_documents) else None
    for entry in entries:
        entry['_route_entries'] = entries
        entry['_route_bm25'] = route_bm25
        chunk_documents = [_tokens(f'{chunk["heading"]} {chunk["text"]}') for chunk in entry['chunks']]
        entry['_chunk_documents'] = chunk_documents
        entry['_chunk_bm25'] = BM25Okapi(chunk_documents) if chunk_documents and any(chunk_documents) else None
    return result


def _config(bot_hash: str) -> dict[str, Any]:
    manager = getattr(OlivOSAIChatAssassin.data, 'gData', None)
    try:
        return manager.getConfig(bot_hash) if manager else {}
    except Exception:
        return {}


def _history_window(history: list[dict[str, Any]], bot_hash: str) -> list[dict[str, Any]]:
    config = _config(bot_hash)
    configured = config.get('history_dynamic_size') if config.get('history_dynamic') else config.get('history_size', 12)
    try:
        size = max(1, int(configured))
    except (TypeError, ValueError):
        size = 12
    return list(history or [])[-size:]


def _query_text(history: list[dict[str, Any]], bot_hash: str) -> str:
    window = _history_window(history, bot_hash)
    messages = [str(item.get('message', '')).strip() for item in window if item.get('message')]
    if not messages:
        return ''
    # 最新消息重复一次是通用的时间衰减，不解析说话者、不拼接定向短句。
    return '\n'.join(messages + [messages[-1]])


def extract_context_keywords(history: list[dict[str, Any]], context_messages: int = 12) -> list[str]:
    text = '\n'.join(str(item.get('message', '')) for item in list(history or [])[-max(1, context_messages):])
    return _tokens(text)


def _rank_documents(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    if not query_tokens or not documents or not any(documents):
        return [0.0] * len(documents)
    return [float(score) for score in BM25Okapi(documents).get_scores(query_tokens)]


def explain_skill_selection(history: list[dict[str, Any]], bot_hash: str) -> list[dict[str, Any]]:
    index = getattr(OlivOSAIChatAssassin.data, 'gSkillsIndex', None) or build_skills_index()
    entries = list(index.values())
    query_text = _query_text(history, bot_hash)
    window = _history_window(history, bot_hash)
    latest_text = next((str(item.get('message', '')) for item in reversed(window) if item.get('message')), query_text)
    query_tokens = _tokens(query_text)
    cache_key = '\x1f'.join(query_tokens)
    cached = _query_cache.get(cache_key)
    if cached and time.time() - cached[0] <= OlivOSAIChatAssassin.data.gSkillsQueryCacheTTL:
        _query_cache.move_to_end(cache_key)
        return [dict(item) for item in cached[1]]
    is_foreign_query = bool(_LATIN_RE.search(latest_text)) and not _CJK_RE.search(latest_text)
    translated_query = _translate_foreign_query(latest_text) if is_foreign_query else ''
    query_tokens = _tokens(f'{query_text}\n{translated_query}')
    documents = [entry['route_tokens'] for entry in entries]
    lexical = _rank_documents(query_tokens, documents)
    for index, entry in enumerate(entries):
        declared_tokens = set(_tokens(f'{entry["name"]} {entry["description"]} {entry["metadata_terms"]}'))
        lexical[index] += len(set(query_tokens) & declared_tokens) * 1.5
    config = _config(bot_hash)
    try:
        match_rate = max(0.0, float(config.get('skills_match_rate', 0.12)))
    except (TypeError, ValueError):
        match_rate = 0.12
    best_score = max(lexical, default=0.0)
    absolute_threshold = max(0.25, match_rate * 10.0)
    ranked = sorted(
        (
            {'skill': entry['name'], 'score': score, 'reason': 'BM25：标准历史窗口与 Skill 自有元数据/正文'}
            for entry, score in zip(entries, lexical)
            if score >= max(2.0, absolute_threshold) and score >= best_score * 0.82
        ),
        key=lambda item: (-item['score'], item['skill']),
    )
    if not ranked:
        result = []
        _query_cache[cache_key] = (time.time(), result)
        return result
    limit = max(1, int(config.get('skills_max_matches', 2)))
    result = ranked[:limit]
    _query_cache[cache_key] = (time.time(), [dict(item) for item in result])
    _query_cache.move_to_end(cache_key)
    while len(_query_cache) > OlivOSAIChatAssassin.data.gSkillsQueryCacheSize:
        _query_cache.popitem(last=False)
    return result


def _rank_chunks(
    query_tokens: list[str],
    chunks: list[dict[str, Any]],
    bm25=None,
    priority_tokens: list[str] | None = None,
) -> list[dict[str, Any]]:
    scores = [float(score) for score in bm25.get_scores(query_tokens)] if bm25 else _rank_documents(
        query_tokens,
        [_tokens(f'{chunk["heading"]} {chunk["text"]}') for chunk in chunks],
    )
    query_set = set(query_tokens)
    ranked = []
    for chunk, score in zip(chunks, scores):
        if score <= 0:
            continue
        heading_tokens = set(_tokens(chunk['heading']))
        heading_overlap = len(query_set & heading_tokens)
        # 标题是 Markdown 资料最可靠的结构信号，显著高于正文中的偶然词频。
        adjusted = score + heading_overlap * 200.0
        text = chunk['text'].strip()
        normalized_text = text.lower()
        direct_hits = sum(1 for token in query_set if len(token) >= 2 and token in normalized_text)
        adjusted += direct_hits * 300.0
        adjusted += sum(
            1000.0
            for token in set(priority_tokens or [])
            if len(token) >= 2 and token in normalized_text
        )
        # 通用结构降权：目录/总览型长列表不应压过具体章节。
        line_count = max(1, len(text.splitlines()))
        list_ratio = sum(line.lstrip().startswith(('-', '*', '|')) for line in text.splitlines()) / line_count
        if list_ratio > 0.65 and heading_overlap == 0:
            adjusted *= 0.55
        if len(text) < 40 and heading_overlap == 0:
            adjusted *= 0.65
        heading_level = len(chunk.get('heading_markup', ''))
        if heading_level == 1 and heading_overlap == 0:
            adjusted *= 0.35
        ranked.append({**chunk, 'score': adjusted})
    ranked.sort(key=lambda item: (-item['score'], item['source'], item['heading']))
    return ranked


def retrieve_skill_context(history: list[dict[str, Any]], bot_hash: str) -> dict[str, Any]:
    window = _history_window(history, bot_hash)
    selections = explain_skill_selection(history, bot_hash)
    index = getattr(OlivOSAIChatAssassin.data, 'gSkillsIndex', None) or build_skills_index()
    latest = next((str(item.get('message', '')) for item in reversed(window) if item.get('message')), '')
    latest_tokens = {token for token in _tokens(latest) if len(token) >= 2}
    focus_messages = [latest]
    for item in reversed(window[:-1]):
        message = str(item.get('message', ''))
        if latest_tokens & set(_tokens(message)):
            focus_messages.append(message)
        if len(focus_messages) >= 4:
            break
    priority_tokens = _tokens('\n'.join(reversed(focus_messages)))
    query_tokens = _tokens(f'{latest}\n{latest}\n{_query_text(history, bot_hash)}')
    chunks = [chunk for selection in selections for chunk in index[selection['skill']]['chunks']]
    selected_entry = index[selections[0]['skill']] if len(selections) == 1 else None
    chunk_bm25 = selected_entry.get('_chunk_bm25') if selected_entry else None
    ranked = _rank_chunks(query_tokens, chunks, chunk_bm25, priority_tokens=priority_tokens)
    if ranked:
        relevance_floor = ranked[0]['score'] * 0.18
        ranked = [chunk for chunk in ranked if chunk['score'] >= relevance_floor]
    max_chars = max(0, int(_config(bot_hash).get('skills_max_chars', 2000)))
    chosen, used = [], 0
    seen_chunks = set()
    for chunk in ranked:
        content = chunk['text'].strip()
        normalized = re.sub(r'\s+', ' ', content).strip().lower()
        fingerprint = normalized[:500]
        if not fingerprint or fingerprint in seen_chunks:
            continue
        seen_chunks.add(fingerprint)
        if chosen and used + len(content) > max_chars:
            continue
        if not chosen and len(content) > max_chars:
            content = content[:max_chars].rstrip()
        chosen.append({**chunk, 'text': content})
        used += len(content)
        if used >= max_chars or len(chosen) >= 4:
            break
    return {
        'history': window,
        'selections': selections,
        'chunks': chosen,
        'query_terms': query_tokens,
    }


def get_skills_context(history: list[dict[str, Any]], bot_hash: str) -> str:
    if not _config(bot_hash).get('skills_enable', True):
        return ''
    trace = retrieve_skill_context(history, bot_hash)
    blocks = [
        f'[Skill: {chunk["skill"]} | Source: {chunk["source"]} | Section: {chunk["heading"]}]\n{chunk["text"]}'
        for chunk in trace['chunks']
    ]
    context = '\n\n'.join(blocks)
    _log(f'[Skills V3] selected={trace["selections"]} chunks={len(trace["chunks"])} chars={len(context)}')
    return context
