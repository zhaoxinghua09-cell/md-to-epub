# 一键电子书 · Markdown → EPUB
## 许可说明 · License Notice

- **权利状态**：本仓库以 **MIT 许可** 许可发布，可依该许可证条款自由使用、修改与再分发。
- **引用建议**：引用时请标注仓库名与原文链接 `https://github.com/zhaoxinghua09-cell/md-to-epub`
  与权利人「赵兴华 / Steven Zhao·China」。
- **品牌状态限定**：MedXpert、SynomosAI、LGD 等为相关项目标识，
  **均未申请实体注册、未申请商标注册**；出现仅作来源标识，
  不构成对法人实体或商标权的任何主张。
- **完整条款**：见仓库根目录 [LICENSE](LICENSE)。
- **联系**：zhaoxinghua06@126.com ｜ ORCID 0009-0001-0512-1237

---


**One-Click eBook: Markdown to EPUB** —— 把研究报告、合规手册、公众号合集、会话纪要，一条命令变成 Kindle / Apple Books / Google Play Books / Kobo 通吃的 EPUB3 电子书。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB)
![Deps](https://img.shields.io/badge/deps-zero%20stdlib-brightgreen)
![Platform](https://img.shields.io/badge/Platform-Win%20%7C%20macOS%20%7C%20Linux-lightgrey)
[![LGD-Powered](https://medxpert.cn/badge/powered/svg/lgd-powered-en.svg)](https://medxpert.cn)

> **一句话主张**：别人让你装 3 个 pip 包，我们只要一个能开机的 Python——同一输入永远同一本书，坏输出永远盖不掉好输出。
>
> 关键词：Markdown 转 EPUB / ebook 转换 / 电子书生成 / Kindle 中文排版 / EPUB3 / docs-as-code 出版 / 离线零依赖 / md-to-epub / markdown2epub / tech book publishing

快速链接：演示样张 [samples/demo_zh.md](samples/demo_zh.md) · 自检 `python test_forge.py` · 反馈 Issues

**安装**（复制整个目录即可，无 pip install）：

```bash
# WorkBuddy
cp -r md-to-epub ~/.workbuddy/skills/
# Claude Code / Cursor / opencode 同理，见下方安装矩阵
```

| Agent | 技能目录 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/md-to-epub/` |
| Claude Code | `~/.claude/skills/md-to-epub/` |
| opencode | `~/.config/opencode/skills/md-to-epub/` |
| ima / Cursor | 项目根 `.workbuddy/skills/` 或对应 skills 目录 |

试用一条命令：

```bash
python scripts/epub_forge.py all samples/demo_zh.md -o demo.epub --author "你的名字"
```

---

## 为什么用它（与上游 smerchek/claude-epub-skill 的差异）

| 能力 | 本技能 v1.0.0 | 上游 claude-epub-skill |
|---|---|---|
| 依赖 | **零**（纯标准库 zipfile） | ebooklib + markdown2 + Pygments |
| 中文排版 | **自动**：宋体栈+首行缩进 2em+1.8 行高 | 英文样式，中文无优化 |
| 本地图片嵌入 | **真实现**（自动收集+重写+mediatype） | README 声称支持，代码未实现 |
| 可复现 | **JSON IR 中间层**，md→IR→EPUB 两段可查 | 单段黑盒 |
| 输出安全 | **原子写+自校验+last-good** | 直接覆盖 |
| 报错 | **JSON 修复回执**（规则码+证据+修复建议） | Python 堆栈 |
| 封面 | `--cover` 一参数进 manifest | 无 |
| 离线/免 API | 完全离线 | 完全离线 |

## 管线（确定性输出层）

```
Markdown ──parse──▶ JSON IR（章节/元数据/图片清单，落盘可查）──forge──▶ EPUB3
         └──────── all = parse + forge 一步到位 ────────┘
```

- **LLM 只准备输入与参数，编译全由引擎完成**——同输入同输出，可机器校验。
- 输出先写临时文件 → zip 结构自校验（mimetype 首条 STORED / container / opf / nav / ncx / 章节数）→ 全过才原子替换旧版（last-good）。
- stdout 只出 JSON 回执；错误统一 `{"error":{"code","subject","evidence","fixes"}}`。

## 错误码 → 处置对照表

| 规则码 | 含义 | 处置 |
|---|---|---|
| `E_INPUT_MISSING` | 输入文件不存在 | 检查路径（注意中文路径） |
| `E_INPUT_EMPTY` | 输入为空/只有 frontmatter | 补正文 |
| `E_META_MISSING` | 无书名且无法自动推断 | frontmatter 加 `title:` 或 `--title` |
| `E_CODE_UNCLOSED` | 代码块未闭合 | 补闭合 ``` 或删除 |
| `E_IMAGE_MISSING` | 引用的本地图片不存在 | 补图/改相对路径/删语法 |
| `E_IMAGE_UNSUPPORTED` | 图片格式不支持 | 转 png/jpg/gif/svg/webp |
| `E_COVER_MISSING` | 封面文件不存在 | 核对 --cover 路径 |
| `E_IR_INVALID` | IR 结构非法 | 用 parse 重新生成 IR |
| `E_OUTPUT_MISSING` | 未指定 -o | 补 `-o book.epub` |
| `E_VALIDATE_FAILED` | 自校验未过（罕见） | 重跑；复现则带 IR 反馈 |
| `E_WRITE_FAILED` | 写盘失败 | 检查目录可写/磁盘空间 |
| `E_UNEXPECTED` | 未预期异常 | 带完整回执反馈 |

## 支持的 Markdown 子集

标题(H1-H6) / 段落 / **粗体** / *斜体* / `行内码` / 链接 / 本地图片(png·jpg·gif·svg·webp) / 有序·无序·两层嵌套列表 / 表格(带分隔行校验) / 引用块 / 水平线 / 围栏代码块 / YAML frontmatter(title·author·language·date)。

不做：内容创作、mobi、数学公式、PDF。Kindle 阅读器已原生支持 EPUB，无需 mobi。

## 开发者

```bash
python test_forge.py          # 24 项自检（零依赖）
python scripts/epub_forge.py --help
```

## 致谢与许可

衍生自 [smerchek/claude-epub-skill](https://github.com/smerchek/claude-epub-skill) (MIT)——感谢原项目的 EPUB3 结构设计。本项目重写了编译引擎（零依赖 + 中文排版 + IR 管线 + 原子校验），以 MIT 继续开源。

**© SynomosAI** · 诺声(Logos)@SynomosAI 出品 · [LGD-Powered](https://medxpert.cn)

社区：Issues 反馈优先；中文用户可经 WorkBuddy 技能市场获取后续版本。
