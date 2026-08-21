# ADR-0018：Token 统计把缓存计入总量，并接入 IvyeaAgent / DeepSeek Harness

- **日期**：2026-08-21
- **状态**：已采纳
- **依据**：本机全量归档库 `data/token_archive.sqlite3` 实测对比

## 背景

服务器监控的「Token 使用量统计」长期给出一个反直觉的结论：Claude Code 用了几个月，
用量却只有 Codex 的零头。用户凭直觉觉得不对，查下来是真错了，而且方向是反的。

根因是 `total_tokens` 只算 `input + output`，把缓存读写整个丢掉了。这在单一工具内部
无伤大雅，跨工具比就致命 —— 各家上报口径根本不一样：

- **Claude Code** 的 jsonl 把上下文拆进 `cache_read_input_tokens` /
  `cache_creation_input_tokens`，裸 `input_tokens` 每次请求只有几个 token。
- **Codex** 的 rollout 上报的 `total_token_usage.input_tokens` **本身就含
  `cached_input_tokens`**。本机实测：10.90 亿 input 里有 10.52 亿是缓存，占 96.5%。

于是同一份缓存，在 Codex 那边全额进总量，在 Claude 那边被抹成零。本机全量历史对比：

| Agent | 旧口径 | 实际（含缓存） | 低估倍数 |
|---|---|---|---|
| Claude Code | 1.07 亿 | **320.0 亿** | 298× |
| Codex | 19.0 亿 | 19.0 亿 | 1× |
| Hermes | 1910 万 | 2.57 亿 | 13× |

排行榜从"Codex 是 Claude 的 18 倍"翻转成"Claude 是 Codex 的 17 倍"。

同时暴露出另外三个问题：

1. **价目表把 Anthropic 的价写成了作废的旧价**（opus 15/75），实际官方是 5/25，
   opus 成本被整体高估 3 倍；而 `claude-opus-5` / `claude-fable-5` / `claude-sonnet-5`
   压根不在表里，落到 `_DEFAULT_PRICE (1,4)`，反向低估 5~10 倍。
2. **模型归属取错**。旧扫描器取会话文件里第一个 `message.model`，会取到 Claude Code
   为配额提示/报错本地构造的 `<synthetic>` —— 本机有 55 亿 token 挂在这个不存在的
   "模型"名下。
3. **两个真实在用的工具从来没接入过**：IvyeaAgent（`~/.ivyea/sessions/*.json`，现在
   是自动链路的主脑）和 DeepSeek Harness（`~/.dsh/sessions/**/session.jsonl.zstd`）。

## 决策

1. **`total_tokens` 一律等于 `输入 + 输出 + 缓存读 + 缓存写`**，日/周/月、模型、Agent
   全部改成这个口径，界面上把「缓存」单列成一列，别让新口径又变成另一个黑箱。
2. **价目表按官方价目重写**，并补齐现役型号（Fable 5 / Opus 5 / Sonnet 5 /
   deepseek-v4-* / grok / gpt-5-codex）。
3. **模型归属改成"按 token 量取众数"**，并跳过 `<` 开头的合成名。归档库里已经落库的
   `<synthetic>` 行合并进 `claude-code`（归档只有天粒度，对不回原始型号，退一步给一个
   真实存在、同价档的归属），迁移幂等且 token/会话数守恒。
4. **新增两个数据源**：IvyeaAgent 与 DeepSeek Harness，路径同样走 hub_settings，
   跟其余集成一个模式。

## 理由

- **数字会突然变大一两个量级**，包括顶栏的本月 token。这是修正不是回归，所以在卡片上
  显式写「含缓存读写」，并给明细表加「缓存」列，让人能自己对上账。
- **归档里的 Codex 历史行 cache 列是 0**，因为当时就没拆开存。改口径后 Codex 数字不变、
  Claude 暴涨，这是对的 —— Codex 的缓存本来就已经算在 input 里了。
- **dsh 的会话是 zstd 压缩的**，而 IvyeaOps 跑在系统 python 上、没有 `zstandard` 包。
  选择走 `zstd -dc` 子进程而不是加依赖；没有这个二进制就整源跳过并在覆盖表里写明，
  绝不静默按 0 计。

## 后果

- **Claude 会话按文件 mtime 分桶**，一个横跨数月的长会话，全部 token 会记在最后一次
  修改的那天。总量不受影响，但日/周分布会糊。要修得改成按每条消息自带的 timestamp
  分桶，会连带改变"会话数"的语义，单独一轮做。
  - 连带纪律：**不要用大 lookback 重跑归档**（`archive_run(lookback_days=730)`）。
    mtime 会把老会话重新记到今天，和已有的历史行叠加，越跑越多。保持默认 7 天窗口。
- **控制台自己直连自定义 API 的调用没有埋点**（`assistant.py` /
  `ai_synthesis_service.py` / `compactor.py`）。它们全是 `stream: true` 且没带
  `stream_options.include_usage`，**API 根本不返回 usage**，属于没有数据可扫，要补得
  先改调用方把用量记下来。
