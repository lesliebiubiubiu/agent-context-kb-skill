# Agent Context KB

[English](README.md) | **中文**

一个轻量的、面向 agent 的路由式项目知识库，为 coding agent 提供持久、共享的项目记忆。
Agent 在动手*之前*先读它、完成*之后*写回它——上下文因此跨 session、跨不同的
agent 得以延续。

## 问题所在

Coding agent 每次都是冷启动。每个新 session——以及每个不同的 agent——打开你的
项目时对它一无所知：系统是怎么搭起来的、为什么这样搭，哪些事已有定论、哪些路
已被排除、哪些 bug 已经被艰难地查过一遍。

常见的补救办法在项目变大后全都会垮掉。把一切塞进 `AGENTS.md`/`CLAUDE.md`？整个文件会被
全量注入每一个 session——它只能越长越长，直到信号被淹没、agent 开始无视它。
写设计文档？agent 不知道三十篇文档里哪一篇能回答今天的问题——而且一半已经过
期。每次人肉粘贴上下文？那*你*就成了记忆本体，对每个新 session、每个新工具把
自己重复一遍。而最经不起丢失的那部分知识——你追了两小时的 bug、那个看起来对
其实错的方案——从来就不在文档里；它随着刚刚结束的那个 session 一起蒸发了。

**Agent Context KB** 把项目记忆组织成一张小而可导航的图，而不是一个平铺的堆。
每类任务都有一个入口，把 agent 路由到与它相关的那几份知识上；agent 一边工作
一边把学到的东西写回去；整个知识库被主动保持紧凑，所以它始终可信，而不会腐
坏。它就是仓库里的纯文本文件，因此每个 agent——Claude Code、Codex，以及之后
出现的任何工具——共享同一份记忆。

## 亮点

- **读得少，读得准。** 每个任务只拉取与它相关的知识——而不是整个知识堆。
- **越长越大，依然紧凑。** 知识库被主动保持精简，因此它始终可信，而不会退化成
  一个没人翻的垃圾堆。
- **沿途留下足迹。** Agent 在工作中把可沉淀的发现写回知识库，来之不易的项目
  上下文因此留给未来的 session 和未来的 agent。

## 它真的有用吗？

项目记忆只有在整条链路都成立时才有回报：知识必须**送达** agent、在开工前被
**读到**、对工作真正**有帮助**、并且在维护中**不腐坏**。大多数知识库工具从未
在任何一环上被检验过。这一个则逐环测量——完整方法与数据见
[`evals/REPORT.md`](evals/REPORT.md)。

- **它被读到了。** 在我们插桩记录的 dogfooding session 中，read
  compliance——agent 是否在第一次源码探索或编辑之前打开了知识库——在我们修复
  协议送达方式后从 33% 升到了 69.6%。
- **它有帮助。** 日常体验里，agent 会从记录的计划处继续、引用已记录的决策而
  不是重新推导——新 session 恰好从上一个 session 停下的地方接着干时，差别最
  为明显。在主观体验之外，项目知识问答也通过了用故意写错的答案校准过的 LLM
  judge。
- **它不腐坏。** 每次 trim 之后都跟一轮非回归检查：清理前答对的问题，清理后
  必须依然通过。

## 我们如何测量

每个 eval 都固定精确的仓库 commit *和*知识库 commit，因此一次运行是可复现
的，而不是"我试的时候是好的"。任务是只读的项目知识问答，配两类检查：

- **确定性的行为检查**，直接从 agent 的 tool-call trace 中读取——它是否打开
  了正确的知识库文件、是否没有编辑任何东西。
- **针对答案本身的语义检查**，由 LLM judge 打分；judge 用故意写错的答案做过
  校准，橡皮图章式的 judge 会在负样本上直接失败。

各 harness 的运行分开统计而不混在一起；整套任务、pin 和 runner 都在
[`evals/`](evals/) 里。

## 快速开始

用 [`skills`](https://github.com/vercel-labs/skills) CLI 把这个 skill 装进你的
agent：

```bash
npx skills add lesliebiubiubiu/agent-context-kb-skill
```

然后在任意仓库里，让你的 agent 完成初始化——比如：

> [!TIP]
> "Initialize an agent-kb knowledge base in this repo."

这会触发 skill：搭建 `.agent-kb/` 目录，并在主 agent 指令文件里加入一段简短
的运行时协议，告诉 agent 怎么使用它：

```
.agent-kb/
├── start.md         # 入口 —— agent 首先读这里
├── routes.yaml      # 任务类型 → 该读哪些文档
├── map.md           # 所有主题的单页总览
├── plans/           # 当前焦点 —— 新 session 接着做，而不是重新开始
├── inbox/           # 速记笔记，之后归档进主题
├── architecture/    # 系统的形状，以及为什么是这个形状
├── decisions/       # 哪些已有定论，哪些已被排除
├── debugging/       # 已经查过的 bug
├── workflows/       # 如何构建、测试、部署
└── conventions/     # 代码风格与项目惯例
```

随后 agent 会提议做一次性的蒸馏：从你的 README、文档和 git 历史里挖出持久的
事实，作为各主题文件的种子。

> [!TIP]
> 默认情况下知识库不进入你项目的 git 历史：它有自己的嵌套仓库
> （`.agent-kb/.git`），且 `.agent-kb/` 被 gitignore——你的项目保持干净，记忆
> 归你所有。如果你希望知识库随仓库一起走、被项目管理起来，就改用共享模式
> （`init --shared`）——各模式的细节见
> [`SKILL.md`](skills/agent-context-kb/SKILL.md)。

## 使用这个 skill

![Agent 通过知识库路由完成任务](https://github.com/lesliebiubiubiu/agent-context-kb-skill/releases/download/readme-assets/demo.gif)

大部分时候你用自然语言驱动它——你的 agent 会挑对命令。几个典型的说法：

- *"Set up an agent-kb knowledge base in this repo."*
- *"Update the agent-kb with what we just figured out."*
- *"Record why we picked Postgres over Mongo here."*
- *"Trim kb"*
- *"Show me the kb stats."*

它们对应到你会直接接触的命令：

| 命令 | 何时使用 |
|---|---|
| `init` | 在一个仓库里初始化知识库 |
| `note` | 记录一条值得保留的持久事实 |
| `trim` | 知识库臃肿时做清理 |
| `stats` | 查看知识库的使用情况 |

其余命令由 agent 在维护知识库时自动运行——`validate`（编辑后检查结构）、
`compile`（把速记笔记归档到正确位置）、`upgrade`（刷新协议）——你很少需要亲
自调用。

### 什么该进知识库

只放持久知识：架构决策、模块边界、debug 结论、约定惯例、集成约束，以及未来
的 agent 应该避开的坑。

> [!TIP]
> 不放进度日志、聊天摘要、密钥，也不放读代码就能看出来的东西——知识库之所以
> 有用，恰恰因为它保持小。

完整协议——路由格式、压缩循环、版本化与隐私模式——见
[`skills/agent-context-kb/SKILL.md`](skills/agent-context-kb/SKILL.md)。

## 更新日志

以 git tag 版本化——每个版本的变更见
[GitHub Releases](https://github.com/lesliebiubiubiu/agent-context-kb-skill/releases)。

## 许可证

[MIT](LICENSE)
