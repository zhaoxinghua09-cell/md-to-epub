---
name: md-to-epub
display_name: 一键电子书·Markdown→EPUB
display_name_en: One-Click eBook: Markdown to EPUB
version: 1.0.0
category: content-creation
platforms: [WorkBuddy, Claude Code, Cursor, ima, opencode]
agent_created: true
author: 诺声(Logos)@SynomosAI
license: MIT
description: 一条命令把 Markdown 变成 Kindle/Apple Books 可读的专业 EPUB 电子书——纯 Python 标准库零依赖、离线免 API、中文排版自动优化（思源宋体栈+首行缩进）、本地图片自动嵌入、EPUB3 结构自校验原子落盘。研究报告/合规手册/公众号合集/会话纪要一键成书，转换（convert）、电子书（ebook）、EPUB3、Kindle、MOBI 替代、中文排版、格式转换、技术文档出版、docs-as-code 都找它。衍生自 smerchek/claude-epub-skill (MIT)。
tags: [电子书, ebook, epub, kindle, markdown, 转换, convert, 中文排版, 排版, 出版, 离线, zero-dependency, docs-as-code]
slug: md-to-epub
---

# 一键电子书 · Markdown → EPUB

> 把研究报告、合规手册、公众号合集、会话纪要，一条命令变成 Kindle / Apple Books / Google Play Books / Kobo 通吃的 EPUB3 电子书。
> **纯 Python 标准库 · 零依赖 · 离线 · 中文排版自动优化。**

## 什么时候用我

- 用户说"把这份文档/报告/合集做成电子书 / 转 EPUB / 放 Kindle 里看"
- 用户给了一个 `.md` 文件（或一段 markdown 内容）想要可下载、可分发的成品书
- 批量把技术文档变成可携带阅读格式（docs-as-code 出版流）

## 怎么用（确定性引擎，LLM 不产最终产物）

**你（LLM）只做两件事**：① 拿到 Markdown 内容并保存为 `.md` 文件；② 组装命令行参数。**编译一律交给引擎**：

```bash
python scripts/epub_forge.py all <input.md> -o <output.epub> --title "书名" --author "作者"
```

### 子命令

| 子命令 | 作用 | 典型场景 |
|---|---|---|
| `all` | md → IR → EPUB 一步到位 | 默认用这个 |
| `parse` | md → JSON IR（中间表示，落盘 `.ir.json`） | 想先检查章节结构再出书 |
| `forge` | IR JSON → EPUB | 改完 IR/元数据后重编译 |

### 参数

| 参数 | 说明 |
|---|---|
| `-o` | 输出 `.epub` 路径（all/forge 必填） |
| `--title` / `--author` | 书名/作者（缺省取 frontmatter 或首个 H1） |
| `--lang` | 语言码（缺省自动检测：中文占比>30% → `zh-CN`，自动切中文排版） |
| `--cover` | 封面图（png/jpg/gif/svg/webp，自动嵌入 manifest） |
| `--css` | 自定义 CSS（覆盖内置中/英样式） |
| `--ir` | IR 落盘路径（默认与输出同名 `.ir.json`） |

### 回执（stdout 只出 JSON）

成功：

```json
{"ok": true, "mode": "all", "output": "D:/…/book.epub", "validated": true,
 "chapters": 5, "images": 2, "cover": false, "css": "zh", "elapsed_ms": 87}
```

失败（修复回执，规则码见 README 错误码表）：

```json
{"error": {"code": "E_IMAGE_MISSING", "subject": "cover.jpg",
           "evidence": "本地图片不存在: D:/…/cover.jpg",
           "fixes": ["补上图片文件", "改用相对路径", "删掉该图片语法"]}}
```

## 中文排版（默认自动）

中文稿自动切 `zh` 样式：思源宋体/Noto Serif CJK/宋体正文栈、黑体标题栈、**首行缩进 2em**、1.8 行高、章自动分页；英文稿用 Georgia 栈。可用 `--lang en` 强制切换。

## 能力边界

- 支持：ATX 标题(H1-H6)、段落、粗/斜、行内码、链接、**本地图片嵌入**(png/jpg/gif/svg/webp)、有序/无序/两层嵌套列表、表格（含分隔行校验）、引用块、水平线、围栏代码块、YAML frontmatter(title/author/language/date)
- 不做：内容创作（先写好 md）、mobi 转换（Kindle 已原生支持 EPUB）、数学公式渲染、PDF 输出

## 环境要求

Python 3.8+，零第三方依赖，无需 pip install。
