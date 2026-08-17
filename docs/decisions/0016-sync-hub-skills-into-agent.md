# ADR-0016 · Skill 中心的 amazon 技能挂给 IvyeaAgent

- **日期**：2026-08-17
- **状态**：已采纳（**方案在同日被简化**，见文末「后续」）

## 背景

这台机器上有**两套技能库**，格式不是一套，数量差十几倍：

| | 位置 | 格式 | 数量 |
|---|---|---|---|
| Skill 中心 | `{data_dir}/skills` | SKILL.md + YAML frontmatter（Hermes 时代的产物） | 98 |
| IvyeaAgent | `~/.ivyea/skills` + 包内 `skills_builtin` | SKILL.md + 旁边一个 `skill.json` | 7 |

agent 加载用户技能的方式是扫 `~/.ivyea/skills/**/skill.json`（`ivyea_agent/skills.py`
的 `_iter_user`），按 id 索引。所以 Skill 中心里的 98 个技能，agent 从来查不到，
**任务台的自动匹配永远匹配不到它们**，而界面上没有任何地方解释这件事。

Skill 中心只好绕过去：把 SKILL.md 正文整篇塞进 system 上下文当说明书，并叮嘱模型
「不要去文件系统里找这个 skill 的目录 —— 它不在你的技能库里」。

这句叮嘱有代价，而且代价比看上去大。全库 98 个技能里 **65 个带附属文件**（共 620 个：
463 份参考文档、54 个 Python 脚本、7 个 shell 脚本、LaTeX 模板…），**58 个的正文明确
引用了这些文件**（"运行 `scripts/render.py`"、"规格见 `references/spec.md`"）。要同步的
amazon 域 5 个技能里就有 46 个附属文件、22 处引用。

也就是说：说明书给了，材料扣下了，还禁止它去拿。这不是"两个库没打通"的小别扭，是执行
结果不可信。

## 决策

新增 `services/skill_sync.py`，把 **amazon 域**的技能连同附属文件同步进 agent 的技能库，
并生成 `skill.json`。启动时同步一次，Skill 中心的写操作后台重新同步，另有手动入口。

**只同步 amazon，且维持只读。**

## 理由

**为什么不全同步**：全库有 apple / gaming / creative / data-science 这些跟亚马逊运营
毫无关系的分类。agent 的匹配是全库打分（`skills.search`），候选从 7 变成 98，等于让它
每轮在一堆噪音里挑，匹配质量只会更差。amazon 域 5 个，正好。

**为什么不反过来让 agent 读 frontmatter**：那是改另一个仓库（ivyea-agent）的加载器，
影响所有部署；而 ops 这边做一层转换是本地的、可回滚的。等格式确实要统一时再说。

**为什么脚本仍然只读**：同步过去的是**材料**，不是执行许可。模型能读到 `scripts/*.py`
的内容照着做，但要真跑起来就得放开写权限 —— 那是另一个决定，不该搭着这次一起做。

**目录路径写进正文的头部而不是别处**：自动匹配走 `context_for_query`，它把正文截到 700 字。
写在末尾的说明进不了上下文，等于没写。

## 后果

- 任务台能匹配到的技能从 7 个变成 13 个，中文提问实测能命中（"帮我分析广告搜索词报表" →
  `amazon.amazon_ad_campaign_optimization_xlsx`）。
- **中文匹配靠的是短触发词**。agent 的 `_terms()` 是 `[\w一-鿿+.-]+`，**不分词** ——
  一整句中文会被当成一个词，命中率约等于零。真正让中文query 匹配上的是 triggers 里那些
  两三个字的短词（`tl in query` 命中 +3 分），内置技能就是这么写的。同步时会按一份领域词表
  自动配，作者也可以在 frontmatter 里写 `triggers:` 自己接管。
- 危险动作有三处护栏，都有测试守着：**内置技能不能被顶掉**（同 id 时同步产物加 `_hub` 后缀，
  因为 `list_skills` 里 user 覆盖 builtin，静默顶替最难查）；**手工技能不能被误删**
  （只删带 `.synced-from-hub` marker 的目录 —— `~/.ivyea/skills/amazon/listing_image`
  是手工放的）；**同名技能不静默互相覆盖**（跳过并报错）。
- 踩到一个坑：清理旧产物时 `rglob` 是惰性的，边遍历边 `rmtree` 会在删第二个时抛
  `FileNotFoundError`。必须先 `list()` 再删。测试抓到的。
- Skill 中心的定位随之清晰：它是**作者视角**（创建 / 编辑 / 版本 / 回收站 / 审计），
  运行入口逐步归任务台。能力市场负责"看见和获取"，三者不重叠。
- 未做：非 amazon 域的 93 个技能仍只能在 Skill 中心手动跑；两套格式仍然并存。

## 后续（同日）

本 ADR 落地当天就被简化了。上面那个"复制 + 转格式"的同步器**已经删除**。

原因：格式不统一才是根因，而不是"两个库位置不同"。IvyeaAgent 随后支持了通行的
SKILL.md + frontmatter，并支持挂载外部技能库根目录（见 ivyea-agent 的 ADR-0009），
于是复制、生成 skill.json、往正文塞目录说明、防误删、防覆盖、写操作后重新同步 ——
这一整套全都不需要了。现在 `services/agent_skills.py` 只做一件事：把
`{data_dir}/skills/amazon` 写进 agent 的 `skill_roots`。

**Skill 中心里改完立即生效**，不再有同步延迟。

本 ADR 保留：它记录了为什么会有那层转换，以及为什么"只做 amazon""维持只读"这两个
边界决定至今仍然有效。
