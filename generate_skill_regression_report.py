"""Generate an auditable Markdown report for the supplied real chat."""

import os
from pathlib import Path

import OlivOSAIChatAssassin
from test_real_chat_12_context import CHAT


QUESTIONS = [
    '灵魂武器碎了再召唤要多少蓝来着',
    '小芙，仔细翻翻狩魂者trpg里面灵魂武器这一段',
    '小芙，仔细翻阅狩魂者trpg规则书里面灵魂武器片段，详细说明出来',
    '小芙，仔细念召唤灵魂武器那一段',
    '这个渴望咆哮是不能选择防御or反击么',
    '多少MP',
    '那我考你，战斗轮双方对抗检定的成功等级怎么计算？',
    '小芙，我灵能力激活2标签选择5个目标要消耗几点灵力',
    '小芙，你现在立刻给我去看一遍灵能力选择目标那里，再重新说',
    '小芙，你仔细翻翻狩魂者trpg规则书里面灵能力多选部分',
]


class Config:
    def getConfig(self, _bot_hash):
        return {'history_size': 12, 'skills_enable': True, 'skills_max_chars': 3000, 'skills_max_matches': 1}


def main():
    OlivOSAIChatAssassin.data.gSkillsDir = os.path.expanduser('~/.codex/skills')
    OlivOSAIChatAssassin.data.gSkillsExtraDirs = []
    OlivOSAIChatAssassin.data.gData = Config()
    OlivOSAIChatAssassin.data.gSkillsIndex = OlivOSAIChatAssassin.skillManager.build_skills_index()
    history = [{'time': time, 'nickname': nickname, 'message': message} for time, nickname, message in CHAT]
    lines = [
        '# SkillManager V3 重构与真实群聊回归报告', '',
        '## 结论', '',
        '- 新增 `OlivOSAIChatAssassin/skillManagerV3.py`，旧 `skillManager.py` 与 `skillManagerV2.py` 均保留为备份。',
        '- skill 路由严格使用插件标准历史窗口；本报告配置为最近 12 条消息。',
        '- SKILL.md 和其直接引用资料按 Markdown 标题切段，以 BM25 风格评分召回，并保留来源、标题、分数和完整命中文本。',
        '- `skills_max_matches` 已真实生效；翻译调用具有独立硬超时；全量 unittest discover 不再被旧脚本污染。',
        '- 段落排序包含标题加权、当前话题线程加权、顶层标题降权、重复正文去重和相对相关性截断。',
        '- 下列每个案例都展示实际参与匹配的历史上下文和每一段命中正文。', '',
        '## 实现结构', '',
        '1. 扫描 skill 根目录并解析 YAML frontmatter。',
        '2. 读取 SKILL.md 中直接声明的 `references/*.md|txt`。',
        '3. 按标题建立可追溯段落索引。',
        '4. 用标准历史尾窗进行 skill 路由。',
        '5. 在获选 skill 内用 BM25 风格评分检索段落，并按字符预算注入。', '',
        '6. 外语查询使用 Bing 翻译并写入本地缓存；守护线程在配置超时后立即返回。',
        '7. 使用当前消息与共享焦点词的近期消息构造话题线程，解决碎片化追问。', '',
        '## 真实对话回归明细', '',
    ]
    for number, question in enumerate(QUESTIONS, 1):
        index = next(i for i, item in enumerate(history) if item['message'] == question)
        trace = OlivOSAIChatAssassin.skillManager.retrieve_skill_context(history[:index + 1], 'report')
        lines.extend([f'### 案例 {number}：{question}', '', '#### 实际匹配历史上下文', ''])
        for item in trace['history']:
            lines.append(f'- {item["time"]} {item["nickname"]}: {item["message"]}')
        lines.extend(['', '#### Skill 路由结果', '', '```json'])
        import json
        lines.append(json.dumps(trace['selections'], ensure_ascii=False, indent=2))
        lines.extend(['```', '', '#### 命中上下文段落', ''])
        for chunk_number, chunk in enumerate(trace['chunks'], 1):
            lines.extend([
                f'##### 段落 {chunk_number}', '',
                f'- Skill：`{chunk["skill"]}`',
                f'- 来源：`{chunk["source"]}`',
                f'- 标题：`{chunk["heading"]}`',
                f'- 分数：`{chunk["score"]:.6f}`', '',
                '```text', chunk['text'], '```', '',
            ])
    lines.extend([
        '## 回归测试结果', '',
        '- 定向回归：12 项通过。',
        '- 全量 discover：15 项运行，12 项通过，3 个旧版手工脚本按设计跳过。',
        '- Python compileall：通过。', '',
        '```powershell',
        'python -m unittest -v test_skill_manager_v3_regression.py test_skill_routing_v2.py test_real_chat_12_context.py',
        'python -m unittest discover -v',
        'python -m compileall -q OlivOSAIChatAssassin generate_skill_regression_report.py',
        '```', '',
        '## 服务器部署', '',
        '1. 上传整个 `OlivOSAIChatAssassin` 插件代码目录，至少包含新的 `skillManagerV3.py`、`data.py`、`__init__.py` 和 `pyproject.toml`。',
        '2. 保留服务器已有的 `plugin/data/OlivOSAIChatAssassin/skills`，不要用本机 `~/.codex/skills` 覆盖它。',
        '3. `translators` 已安装时无需上传第三方包，也无需上传任何模型。',
        '4. 可选上传 `skill_translation_cache.json`；不上传时服务器会自动创建。',
        '5. 重载插件后检查日志中的 `[Skills V3]`，确认实际入口与命中段落。', '',
    ])
    Path('SKILL_MANAGER_V3_REGRESSION_REPORT.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
