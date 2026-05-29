# Mnexa

一个用于个人 Markdown 知识库的、有纪律的维基维护工具。你可以把任何文件丢给它——本地文件、文件夹、Google Drive 链接，或者 Granola 会议记录——然后一个大语言模型（LLM）会读取它，并维护一个结构化的维基，包含来源/实体/概念页面、交叉引用、索引和日志。你来策划；LLM 来做记账工作。

该工具实现了 [Andrej Karpathy 的 LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 中的模式——请先阅读该文档，那是设计规范。

## 为什么

大多数 LLM 文档工具都是 RAG（检索增强生成）：在查询时检索文本块，基于这些块生成内容，然后丢弃生成的合成结果。Mnexa 将维基视为一个**持久的、不断累积的产物**——每次摄入只更新一次实体和概念页面，每次查询都针对累积的合成结果运行，而不是从原始来源重新推导。你可以在 Obsidian、Logseq、VS Code 或任何 Markdown 编辑器中打开这个维基。LLM 是维护者，你是策展人。

## 安装

需要 Python 3.12+。在 <https://aistudio.google.com/apikey> 获取 Gemini API 密钥。

```bash
# 从 PyPI 安装
uv tool install mnexa            # 或：pip install mnexa
# 或者用于开发
git clone https://github.com/jiashuoz/mnexa && cd mnexa && uv sync
```

在你的 shell 中或知识库根目录下的 `.env` 文件中设置 `GOOGLE_API_KEY`。参见 [`.env.example`](.env.example)。

## 使用方法

```bash
# 创建一个新的知识库
mnexa init ~/my-vault
cd ~/my-vault

# 摄入任何内容——本地文件、本地文件夹、Google Drive 链接或 Granola
mnexa ingest paper.pdf
mnexa ingest ~/Documents/papers/
mnexa ingest "https://drive.google.com/drive/folders/<id>"
mnexa ingest "https://drive.google.com/file/d/<id>"
mnexa ingest "https://app.granola.ai/notes/<id>"
mnexa ingest granola                              # 你所有的 Granola 笔记
mnexa ingest granola --since 2026-04-01           # 增量摄入

# 向维基提问
mnexa query "这篇论文的主张是什么？"

# 检查维基质量
mnexa lint
```

文件夹摄入支持 `--yes` / `-y` 跳过确认，以及 `--limit N` 限制每次运行处理的文件数。对文件夹重新运行摄入时，会跳过那些来源未发生变化的文件（Drive：按 `modifiedTime`；本地：按内容哈希）。

## 知识库结构

```
my-vault/
├── .git/
├── .gitignore                  # 忽略 .mnexa/ 和 .env
├── .mnexa/                     # Mnexa 本地状态（lint 报告）
├── CLAUDE.md                   # 模式说明——编辑 §6 进行定制
├── raw/                        # 不可变的源文档
└── wiki/
    ├── index.md                # 分类目录
    ├── log.md                  # 仅追加的活动日志
    ├── sources/                # 每个摄入的文档一个页面
    ├── entities/               # 人物、组织、产品、地点
    └── concepts/               # 想法、技术、反复出现的主题
```

每次成功的摄入都会产生一个 git 提交。自由撤销、自由历史、自由差异。

## 工作原理

**摄入**是一个两阶段流水线：

1. **分析**——LLM 读取来源以及模式、索引和明显相关的已有页面。生成结构化分析（实体、概念、主张、矛盾）。内部临时数据。
2. **生成**——LLM 输出用于新建/更新的维基页面的 FILE 块。Mnexa 解析、验证路径和前置元数据，**子串验证每个 `⟦"..."⟧` 来源引用标记是否逐字出现在来源中**，然后原子性地写入并提交。

子串验证器是对抗幻觉的最低防线。如果 LLM 捏造了一个不在来源中的传记细节，标记检查将失败，摄入会中止且不改变磁盘上的任何内容。

**查询**是单次 LLM 调用，针对 `index.md` + 按关键词重叠度排名前 N 的页面，结果流式输出到标准输出，并带有行内 `[[wikilink]]` 引用。来自 Drive 的页面在其前置元数据中带有 `drive_url:`，因此查询答案在相关时会自然地呈现可点击的 Drive 链接——不需要单独的“查找文件”命令。

**Lint** 首先运行确定性检查（损坏的链接、前置元数据、索引/维基同步、孤立页面、无根据的页面、slug 风格），然后进行一次 LLM 调用进行语义检查（矛盾、过时的主张、缺失的页面、slug 拼写错误）。输出：`.mnexa/lint/<timestamp>.md`。

## Google Drive

Drive 是一种传输方式，不是一个独立的概念。同样的 `mnexa ingest` 命令接受 Drive 文件 URL 或文件夹 URL；mnexa 在内存中获取内容、摄入，并将 Drive 元数据（`drive_file_id`、`drive_modified`、`drive_url`、`drive_path`、`mime_type`）存储在生成的来源页面的前置元数据中。原始文件保留在 Drive 中——不会有任何内容下载到 `raw/`。

重新同步是幂等的：再次遍历文件夹时会跳过那些 `drive_modified` 与磁盘上已有内容匹配的文件。来源页面的深度会根据内容自适应——论文会得到完整的结构化页面；税务表格或收据只会得到一个简短的页面，不进行实体/概念合成。

**一次性 GCP 设置**（Drive 需要）：

1. 在 <https://console.cloud.google.com> 创建一个项目并启用 Google Drive API。
2. 创建 OAuth 凭据 → “桌面应用” → 下载 JSON 文件。
3. 在你的 `.env` 中设置 `MNEXA_GOOGLE_CLIENT_ID` 和 `MNEXA_GOOGLE_CLIENT_SECRET`。
4. 在 OAuth 同意屏幕上，设置用户类型 = **外部**，发布状态 = **测试**，范围 = `drive.readonly`，并将自己添加为测试用户。

第一次 Drive 摄入会打开浏览器进行 OAuth 授权；刷新令牌会缓存在 `~/.config/mnexa/google-token.json` 中，之后会静默使用。

## Granola

Granola 会议笔记的工作方式相同：同样的 `mnexa ingest` 命令，传输细节被隐藏。认证仅需 Bearer 令牌——无需 OAuth 流程。

**设置**：

1. 在 <https://app.granola.ai> 生成个人 API 密钥（需要 Business 或 Enterprise 套餐——这是 Granola 侧的限制）。
2. 在你的 `.env` 中设置 `GRANOLA_API_KEY`。
3. `mnexa ingest granola://note/not_<14-char-id>` 摄入单个会议，或者 `mnexa ingest granola` 遍历你所有的笔记列表。（Granola 的网络分享链接 `notes.granola.ai/d/<uuid>` 使用的标识符与 API 不同；你需要 `not_*` 笔记 ID，而不是分享链接。）

这种来源类型的最大好处是**参与者会成为实体页面**。在摄入了 30 个会议之后，`entities/alice-smith.md` 会合成你与她讨论过的每一个主题，并附有来自会议记录的可验证引用。这正是维基模式的用途。

Granola 来源页面的前置元数据：

```yaml
type: source
slug: 2026-04-15-design-review
source_path: granola://not_1d3tmYTlCICgjy
granola_note_id: not_1d3tmYTlCICgjy
granola_created: "2026-04-15T14:00:00Z"
granola_updated: "2026-04-15T15:30:00Z"
granola_url: https://notes.granola.ai/d/<uuid>
attendees: ["Alice Smith", "Bob Jones"]
granola_folders: ["Engineering"]
```

`mnexa ingest granola` 是幂等的——它会遍历笔记列表，读取已有来源页面的前置元数据，并跳过那些 `granola_updated` 没有变化的笔记。使用 `--since YYYY-MM-DD` 只获取在指定日期之后更新过的笔记。

## LLM

通过一个小的 `LLMClient` 协议实现与提供商无关。v0 版本集成了 Google Gemini（默认 `gemini-3-flash-preview`）。设置 `MNEXA_MODEL` 为任何 `gemini-*` 模型；设置 `MNEXA_PROVIDER` 覆盖自动推断。添加 Anthropic 或 OpenAI 大约需要 80 行代码加一个 extras 条目——目前没有提供是因为还没人需要它。

## 状态

|                                            |                                              |
| ------------------------------------------ | -------------------------------------------- |
| `mnexa init`                               | ✅                                            |
| `mnexa ingest`（本地文件/文件夹）          | ✅ —— `.md`、`.txt`、`.pdf`、`.docx`          |
| `mnexa ingest`（Google Drive 文件/文件夹） | ✅ —— 自适应深度、幂等重新同步                |
| `mnexa ingest`（Granola 会议笔记）         | ✅ —— 单个笔记或完整列表，通过 `--since` 增量 |
| `mnexa query`                              | ✅                                            |
| `mnexa lint`                               | ✅                                            |
| `mnexa lint --fix`                         | 尚未支持（v0.1）                             |
| 将查询答案保存为维基页面                   | 尚未支持（v0.1）                             |
| Anthropic / OpenAI 提供商                  | 尚未支持                                     |
| Notion / 其他来源                          | 计划中                                       |

## 开发

```bash
uv sync --all-extras
uv run pytest         # 54 个测试
uv run ruff check .
uv run pyright        # 严格模式
```

提示词以文件形式存放在 [`src/mnexa/prompts/`](src/mnexa/prompts) 中，通过 `importlib.resources` 加载。编辑它们，重新运行，迭代。

## 设计要点

- **纯 Markdown 是规范存储。** 没有 SQLite，没有向量索引，没有 FTS5。Karpathy 的 gist 认为在中等规模下 `index.md` 就足够了；在测量数据证明需要更复杂方案之前，我们相信这一点。
- **两阶段摄入**借鉴自 [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki)；**确定性检查然后 LLM 检查的 lint 分层**借鉴自 [SamurAIGPT/llm-wiki-agent](https://github.com/SamurAIGPT/llm-wiki-agent)。子串基础验证器是原创的——上述两个参考项目都没有实现它。
- 原子性写入通过“分阶段写然后重命名”+ 失败时 `git checkout HEAD --` 回滚实现。git 提交是持久性屏障。
- 在我们当前的模式大小（约 3k token，低于阈值）下，Gemini 上下文缓存是无操作的。但协议中仍然表达了意图，以便其他提供商可以实现它。