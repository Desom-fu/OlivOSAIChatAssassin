# `data.py` 字段与配置参考

## 1. 文档范围

本文说明 `OlivOSAIChatAssassin/data.py` 中的全部全局变量和 `configDefault` 配置字段。

字段分为两类：

- `g...` 开头：插件内部运行时状态，不应写入 `config.json`。
- `configDefault`：用户配置默认值，会自动合并到实际的 `config_<botHash>.json`。

配置文件默认位于：

```text
./plugin/data/OlivOSAIChatAssassin/
```

多机器人环境优先使用：

```text
config_<botHash>.json
memory_<botHash>.json
```

## 2. 运行时全局变量

| 字段 | 默认值 | 用途 |
|---|---|---|
| `gProc` | `None` | OlivOS 插件进程对象，用于日志和主动发送消息。 |
| `gPluginName` | `群聊刺客` | 日志与主动消息使用的插件显示名。 |
| `gMessageHistory` | `{}` | 按群号保存运行时消息历史，每个群对应一个 `DynamicQueue`。 |
| `gData` | `None` | 全局 `DataManager`，负责按机器人加载、热更新和保存配置与记忆。 |
| `gConfigDir` | 数据目录 | 配置文件所在目录。 |
| `gConfigPath` | `config.json` | 旧版公共配置路径；新版主要通过 `gConfigDir` 组合路径，目前没有直接使用。 |
| `gGroupLock` | `{}` | 每群一个 `SlackableFairLock`，用于合并连续消息、避免同群并发调用模型。 |
| `gGroupKnowledgeCounter` | `{}` | 遗留字段，当前没有实际使用。 |
| `gGroupKnowledgeCounterLimit` | `16` | 遗留知识计数上限，当前没有实际使用。 |
| `gMemoryLock` | 线程锁 | 防止不同线程同时修改或写入记忆文件。 |
| `gMemoryDir` | 数据目录 | 记忆文件所在目录。 |
| `gMemoryPath` | `memory.json` | 旧版公共记忆路径；新版主要通过 `gMemoryDir` 组合路径。 |
| `gMemoryDefault` | 默认记忆对象 | 新机器人记忆文件的基础结构。 |
| `gMemoryDefaultStr` | `择机加入对话` | 某个群还没有摘要时使用的默认群记忆。 |
| `gHistoryDir` | `history` | 分群历史 JSON 文件目录。 |
| `gHistoryLock` | 线程锁 | 防止并发读写历史文件。 |
| `gHistoryLoaded` | `False` | 标记历史目录是否已经载入，避免每条消息重复读取。 |
| `gStaticKnowledgeDir` | `Knowledge` | 静态 JSON 知识库目录。 |
| `gStaticKnowledge` | `{}` | 已载入内存的静态知识集合。 |
| `gSkillsDir` | `skills` | 主 Skill 根目录，递归读取 `SKILL.md`。 |
| `gSkillsExtraDirs` | `[]` | 额外 Skill 根目录列表，目前只能由代码设置。 |
| `gSkillsIndex` | `{}` | Skill 元数据、正文分段和 BM25 数据的内存索引。 |
| `gSkillsQueryCacheSize` | `512` | Skill 路由查询缓存最大条数。 |
| `gSkillsQueryCacheTTL` | `900` | Skill 路由查询缓存有效期，单位秒。 |
| `gSkillsTranslationBackend` | `bing` | 外语 Skill 查询使用的翻译后端。 |
| `gSkillsTranslationFromLanguage` | `auto` | 翻译源语言自动检测。 |
| `gSkillsTranslationToLanguage` | `zh` | 翻译目标语言为中文。 |
| `gSkillsTranslationTimeout` | `5` | 外语翻译硬超时，单位秒。 |
| `gSkillsTranslationCachePath` | 翻译缓存 JSON | 持久化外语查询翻译结果。 |
| `gImageDir` | `Image` | 下载图片和本地图片文件目录。 |
| `gImageCache` | `{}` | 按群保存近期图片识别结果的运行时 `deque`。 |
| `gPeakUpCache` | `{}` | 普通知识模糊匹配结果缓存，与 DeepSeek prompt cache 无关。 |
| `gThinkTS` | `{}` | 记录机器人在每个群最近一次实际回复时间，用于首次意图判断冷却。 |

## 3. 默认记忆结构

```json
{
  "全局": {
    "知识搜索": {},
    "知识缓存": {},
    "人物关系": {},
    "用户侧写": {},
    "图片缓存": {}
  }
}
```

| 字段 | 用途 | 主要来源 |
|---|---|---|
| `全局` | 跨群共享的长期信息容器。 | 插件默认创建。 |
| `知识搜索` | 供模糊检索的手工或外部知识。 | 用户或外部脚本。 |
| `知识缓存` | AI 从群聊中自动提炼的知识。 | 回复后的记忆总结任务。 |
| `人物关系` | 用户昵称、别名和人物关系资料。 | 主要由用户或外部脚本维护。 |
| `用户侧写` | 用户的语言习惯、兴趣等简短描述。 | 记忆总结任务自动更新。 |
| `图片缓存` | 文件名对应的图片内容、意图和类型。 | OCR 或基础图片解析。 |

群聊摘要不存放在 `全局` 内，而是直接以群号作为键：

```json
{
  "123456789": "这个群最近在讨论灵魂武器规则"
}
```

## 4. 主模型配置

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `api_key` | 空 | 主模型 API Key；为空时不调用主回复模型。 |
| `api_base` | `https://api.deepseek.com/v1` | OpenAI 兼容接口根地址，插件追加 `/chat/completions`。建议末尾不带 `/`。 |
| `model` | `deepseek-v4-flash` | 主回复与记忆总结使用的模型 ID。必须与服务端实际名称一致。 |
| `max_tokens` | `2048` | 单次最大输出 token，不限制输入历史。 |
| `temperature` | `0.7` | 正式回复随机性；记忆总结固定覆盖为 `0.7`。 |
| `thinking` | `{"type":"disabled"}` | 主模型思考模式；正式回复按配置使用，意图判断和记忆总结强制关闭。 |
| `reasoning_effort` | `max` | thinking 开启时发送的思考强度，合法值由服务端决定。 |

## 5. 首次意图判断

`first_thinking` 开启后，插件可以先用小模型输出：

```json
{"d":"NEXT","i":""}
```

或者：

```json
{"d":"SKIP","i":""}
```

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `first_thinking` | `false` | 是否先判断本次消息是否值得调用正式主模型。 |
| `first_thinking_cooldown` | `60` | 最近一次实际回复后，重新允许首次判断所需秒数。 |
| `intent_api.enable` | `false` | 是否为首次判断使用独立 API；关闭时继承主模型配置。 |
| `intent_api.api_key` | 空 | 独立意图模型 Key；为空时保留主 Key。 |
| `intent_api.api_base` | SiliconFlow | 独立意图模型接口。 |
| `intent_api.model` | `Qwen/Qwen2.5-7B-Instruct` | 意图分类模型。 |
| `intent_api.max_tokens` | `16` | 分类 JSON 最大输出 token；被截断时可提高到 32 或 64。 |
| `intent_api.temperature` | `0.0` | 分类任务使用确定性输出。 |
| `intent_api.timeout` | `45` | 首次意图判断请求超时秒数。超时、网络错误或解析失败时默认 `PASS`，继续调用正式主模型，避免门卫故障把整次回复掐掉。 |

首次意图判断若超时、网络异常、API 报错或返回无法识别内容，会默认 `PASS` 并继续正式回复；只有明确返回 `SKIP` 才会跳过。

只有同时满足以下条件，独立意图 API 才会工作：

```json
{
  "first_thinking": true,
  "intent_api": {
    "enable": true
  }
}
```

## 6. 人格、记忆与知识

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `personality` | 默认人格文本 | 注入正式回复和首次意图判断的 system prompt。频繁修改会破坏 DeepSeek 前缀缓存。 |
| `record_knowledge` | `true` | 是否自动提炼 `知识缓存`；关闭后仍会更新用户侧写和群摘要。 |
| `search_knowledge_deepin` | `1` | 在直接命中知识后继续关联检索的层数；建议 0 或 1。 |
| `search_ageing` | `900` | `gPeakUpCache` 模糊匹配结果有效期，单位秒，不是知识本身寿命。 |
| `knowledge_cache_max` | `0` | 自动知识缓存最大条目数；`0` 表示无限。 |

### 6.1 `knowledge_cache_max` 的实际行为

```json
"knowledge_cache_max": 0
```

表示不限制知识缓存条数。

```json
"knowledge_cache_max": 500
```

表示最多保留 500 条：

1. 新知识写入末尾。
2. 已存在的关键词被更新时，会移到末尾，视为最近更新。
3. 超过上限时，从最旧的键开始淘汰。
4. 修改上限并重载插件时，会立即清理已有 `memory.json` 中的超额知识。
5. 非法值和负数按 `0` 处理，即无限，避免误配置导致大规模删除。

示例：

```text
现有顺序：A、B、C
上限：3
更新 A，再加入 D
新顺序：C、A、D
被淘汰：B
```

这里的“最近”指最近写入或更新，不是最近被知识检索命中。

## 7. 群启用与回复触发

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `enabled_groups` | `["all"]` | 允许处理的群号列表；`all` 表示全部群。群号按字符串比较。 |
| `reply_probability` | `1` | 普通消息进入 AI 回复流程的概率，范围建议 0～1。 |
| `reply_keywords` | `[]` | 命中任意子字符串时强制进入回复流程。区分大小写，不是正则。 |
| `mention_reply` | `true` | 被 `[OP:at,id=机器人QQ]` 提及时强制进入回复流程。 |
| `ignore_prefixes` | `[]` | 命中前缀的消息完全忽略，不写历史、不调用 AI。 |
| `retry_count` | `3` | 正式回复没有得到合法 `{"r":[]}` JSON 时的最大尝试次数。 |

触发优先级大致为：

```text
忽略前缀 → 未启用群直接结束
          → 被 @ 强制触发
          → 命中关键词强制触发
          → 否则按 reply_probability 随机判断
```

进入回复流程不等于一定发言，主模型仍可输出：

```json
{"r":[]}
```

## 8. 历史窗口与 DeepSeek 缓存

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `history_size_min` | `5` | 历史长度小于或等于该值时不调用 AI；默认实际需要至少 6 条。 |
| `history_size` | `8` | 主模型历史最低保留量，也是默认 Skill 路由上下文条数。 |
| `history_dynamic` | `false` | 旧动态窗口开关；仍会影响 SkillManager V3 的历史窗口。 |
| `history_dynamic_size` | `16` | 旧动态窗口上限；`history_dynamic=true` 时也成为 Skill 路由窗口。 |
| `prompt_cache_optimized` | `true` | 开启 DeepSeek 前缀缓存友好的分段增长窗口。 |
| `prompt_cache_history_size` | `32` | 缓存优化窗口增长上限。 |

当前历史队列优先级：

```text
prompt_cache_optimized=true
  → 使用 history_size 到 prompt_cache_history_size

prompt_cache_optimized=false 且 history_dynamic=true
  → 使用 history_size 到 history_dynamic_size

两者都关闭
  → 固定 history_size
```

默认历史变化：

```text
8 → 9 → 10 → … → 32 → 8 → 9 → …
```

达到 32 条后的下一条消息到来时，保留原历史最新 7 条，再追加新消息，得到新的 8 条。消息顺序不会颠倒，最新消息仍在末尾。

SkillManager 的一个重要区别：

- `history_dynamic=false`：Skill 路由只使用最近 `history_size` 条。
- `history_dynamic=true`：Skill 路由使用最近 `history_dynamic_size` 条。
- `prompt_cache_history_size` 不会扩大 Skill 路由窗口。

## 9. 连续消息合并

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `slack_time` | `5` | 同群连续消息的初始等待时间，单位秒。 |
| `slack_cooldown_time` | `30` | 一轮连续消息等待的总体冷却约束。 |

用途：

- 等待用户把连续短句发完。
- 让最后一个任务读取完整累计历史。
- 前面的排队任务可以放弃重复回复。
- 减少同群短时间内的重复 API 请求。

数值越大越容易合并消息，但回复延迟也越高。

## 10. 消息长度与图片生命周期

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `max_message_length` | `2048` | 普通单条消息和 AI 输出的最大字符数，不是 token。 |
| `image_expire_time` | `1800` | 历史中的临时图片资源有效期，默认 30 分钟。 |
| `intent_image_cache_size` | `10` | 首次意图模型可参考的全局表情候选数量。 |
| `image_cleanup_non_emoji_time` | `86400` | 非表情图片清理时间，默认 24 小时。 |

图片过期后，历史中的资源会简化为 `[图片]` 或表情摘要，避免模型继续引用失效 URL。

`intent_image_cache_size` 当前主要限制全局补充候选；当前群中的近期表情候选可能全部加入，因此最终候选总数不一定严格等于该值。

## 11. OCR 配置

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `ocr_api.api_key` | 空 | 多模态图片识别 API Key。 |
| `ocr_api.api_base` | SiliconFlow | OCR OpenAI 兼容接口根地址。 |
| `ocr_api.model` | `Pro/moonshotai/Kimi-K2.6` | 需要支持 `image_url` 输入的多模态模型。 |
| `ocr_api.mode` | `base64` | `base64` 时先下载再编码；其他值直接传原始 URL。 |
| `ocr_api.enable` | `false` | 是否调用多模态模型识别图片。 |
| `ocr_api.queue_size` | `8` | 每个群运行时保留的近期图片数量，不是 OCR 并发数。 |

OCR 输出结构：

```json
{
  "content": "图片内容描述",
  "intent": "发送这张图的意图",
  "type": "表情包、梗图、照片或普通图片"
}
```

## 12. Skill 配置

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `skills_enable` | `true` | 是否执行 Skill 路由并注入相关资料。 |
| `skills_max_chars` | `2000` | 单次最多注入的 Skill 正文字符数。 |
| `skills_max_matches` | `2` | 单次最多选择几个 Skill，不是段落数。 |
| `skills_match_rate` | `0.12` | Skill 路由匹配阈值调节参数。 |

`skills_match_rate` 在 V3 中参与：

```text
absolute_threshold = max(0.25, skills_match_rate × 10)
最终最低门槛 = max(2.0, absolute_threshold)
```

因此低于约 `0.2` 时通常仍受固定最低分 `2.0` 限制；提高到 `0.3` 才会明显把绝对门槛提高到约 `3.0`。

## 13. 推荐基础配置

```json
{
  "max_tokens": 2048,
  "temperature": 0.7,
  "thinking": {
    "type": "disabled"
  },
  "first_thinking": true,
  "first_thinking_cooldown": 60,
  "history_size_min": 5,
  "history_size": 8,
  "history_dynamic": false,
  "prompt_cache_optimized": true,
  "prompt_cache_history_size": 32,
  "slack_time": 5,
  "slack_cooldown_time": 30,
  "reply_probability": 1,
  "image_expire_time": 1800,
  "intent_image_cache_size": 10,
  "skills_enable": true,
  "skills_max_chars": 2000,
  "skills_max_matches": 2,
  "knowledge_cache_max": 1000
}
```

`knowledge_cache_max` 的建议：

- 小型私人群：`300～1000`
- 多群长期运行：`1000～5000`
- 需要保留全部知识：`0`

上限越大，`memory.json` 越大，知识模糊检索耗时也越可能上升。

## 14. 修改配置后的生效方式

| 配置类型 | 是否建议重载 |
|---|---|
| API、温度、人格、触发概率 | 支持文件变化检测，但重载更稳妥。 |
| 历史窗口、缓存窗口 | 建议重载，以重建现有 `DynamicQueue`。 |
| `knowledge_cache_max` | 建议重载；加载记忆时会立即清理已有超额知识。 |
| OCR 图片队列长度 | 建议重载，以重建已有 `deque`。 |
| Skill 文件内容 | 重载插件以重新构建 Skill 索引。 |

清理知识时日志示例：

```text
知识缓存已按上限清理: 120 条
```

新知识写入导致淘汰时：

```text
[知识缓存淘汰] - 2 条: 旧关键词A, 旧关键词B
```
