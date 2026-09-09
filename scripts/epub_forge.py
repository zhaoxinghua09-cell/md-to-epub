#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epub_forge.py — md-to-epub 确定性编译引擎（纯 Python 标准库，零依赖）

管线（确定性输出层，LLM 不产最终产物）:
    Markdown 文件 --parse--> JSON IR（中间表示，落盘可查） --forge--> EPUB3
    子命令 all = parse + forge 一步到位

设计规范（技能包设计规范 v1.0，2026-09-08）:
  - 确定性输出层: 同输入同输出; IR 可落盘供校验
  - 原子校验 + last-good: 输出先写临时文件, 自校验全过才原子替换
  - 修复回执: stdout 仅输出 JSON（成功 {"ok":true,...} / 失败 {"error":{...}}）
  - 零依赖: 仅标准库 (zipfile/uuid/hashlib/re/json/argparse)

用法:
  python epub_forge.py all    <input.md>  -o <out.epub> [--title T] [--author A] [--lang zh-CN] [--cover cover.jpg] [--css my.css]
  python epub_forge.py parse  <input.md>  --ir out.ir.json [--title T] [--author A] [--lang zh-CN]
  python epub_forge.py forge  <ir.json>  -o <out.epub> [--cover cover.jpg] [--css my.css]
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone

SKILL_VERSION = "1.0.0"

# ---------------------------------------------------------------- 回执 ----

def emit(obj):
    """唯一 stdout 出口: 纯 JSON 回执。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


class ForgeError(Exception):
    def __init__(self, code, subject, evidence, fixes):
        super().__init__(code)
        self.payload = {"error": {
            "code": code, "subject": subject,
            "evidence": evidence, "fixes": fixes,
        }}


# ---------------------------------------------------- Markdown -> XHTML ----

IMAGE_EXT_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
}

def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def slugify(text, fallback):
    """标题 -> XHTML 安全锚点; 中文标题退化为序号锚点。"""
    s = unicodedata.normalize("NFKC", text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    if not s or not re.match(r"^[A-Za-z]", s):
        s = fallback
    return s


def is_cjk_dominant(text):
    """中文字符占比 > 0.30 判定中文稿（自动切中文排版 CSS）。"""
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    letters = sum(1 for ch in text if ch.isalpha())
    return letters > 0 and (cjk / letters) > 0.30


class Md2Xhtml:
    """实用子集 Markdown -> XHTML（含本地图片收集）。

    支持: ATX 标题 / 段落 / 粗斜体 / 行内码 / 链接 / 图片(本地嵌入) /
          有序·无序·嵌套列表 / 表格(带分隔行校验) / 引用块(多行) / 水平线 / 围栏代码块
    """

    def __init__(self):
        self.images = []          # [(abs_path, media_type, epub_href)]

    # ---------------- inline ----------------
    def _inline(self, text):
        t = esc(text)
        # 图片: ![alt](path) —— 收集本地图片, 重写为 epub 内部引用
        def img_repl(m):
            alt, src = m.group(1), m.group(2)
            if re.match(r"^[a-z]+://|^data:", src, re.I):
                return '<img alt="%s" src="%s"/>' % (alt, esc(src))
            p = os.path.normpath(os.path.abspath(src))
            if not os.path.isfile(p):
                raise ForgeError(
                    "E_IMAGE_MISSING", src,
                    "本地图片不存在: %s" % p,
                    ["补上图片文件", "改用相对路径（相对当前工作目录）", "删掉该图片语法"])
            ext = os.path.splitext(p)[1].lower()
            if ext not in IMAGE_EXT_MAP:
                raise ForgeError(
                    "E_IMAGE_UNSUPPORTED", src,
                    "不支持的图片格式: %s（支持 %s）" % (ext, ", ".join(sorted(IMAGE_EXT_MAP))),
                    ["转成 png/jpg/gif/svg/webp 后重试"])
            idx = len(self.images) + 1
            href = "images/img_%03d%s" % (idx, ext)
            self.images.append((p, IMAGE_EXT_MAP[ext], href))
            return '<img alt="%s" src="%s"/>' % (alt, href)
        t = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img_repl, t)
        # 行内码（先于其他标记, 避免内容被二次渲染）
        codes = []
        def code_repl(m):
            codes.append("<code>%s</code>" % m.group(1))
            return "\x00%d\x00" % (len(codes) - 1)
        t = re.sub(r"`([^`]+)`", code_repl, t)
        # 链接
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"__(.+?)__", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
        t = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", t)
        # 还原行内码
        for i, c in enumerate(codes):
            t = t.replace("\x00%d\x00" % i, c)
        return t

    # ---------------- table ----------------
    @staticmethod
    def _split_row(line):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return cells

    def _table(self, lines):
        header = self._split_row(lines[0])
        if len(lines) < 2 or not re.match(r"^[\s|:\-]+$", lines[1]):
            return None  # 无分隔行, 不当表格
        body = [self._split_row(l) for l in lines[2:]]
        out = ["<table>", "<thead><tr>"]
        for h in header:
            out.append("<th>%s</th>" % self._inline(h))
        out.append("</tr></thead>")
        if body:
            out.append("<tbody>")
            for row in body:
                out.append("<tr>")
                for i in range(len(header)):
                    cell = row[i] if i < len(row) else ""
                    out.append("<td>%s</td>" % self._inline(cell))
                out.append("</tr>")
            out.append("</tbody>")
        out.append("</table>")
        return "\n".join(out)

    # ---------------- block ----------------
    def blocks(self, md_text):
        """Markdown -> XHTML 片段（同时收集图片清单）。"""
        lines = md_text.split("\n")
        out, i, n = [], 0, len(lines)
        para, quote = [], []

        def flush_para():
            if para:
                out.append("<p>%s</p>" % self._inline(" ".join(para)))
                para.clear()

        def flush_quote():
            if quote:
                body = "\n".join(self._inline(q) for q in quote)
                out.append("<blockquote>%s</blockquote>" % body)
                quote.clear()

        while i < n:
            raw = lines[i]
            s = raw.strip()

            # 围栏代码块
            if s.startswith("```"):
                flush_para(); flush_quote()
                lang = s[3:].strip()
                buf = []
                i += 1
                while i < n and not lines[i].strip().startswith("```"):
                    buf.append(lines[i]); i += 1
                if i >= n:
                    raise ForgeError(
                        "E_CODE_UNCLOSED", "代码块起点: ```" + lang,
                        "找到了开栏但全篇没有闭合的 ``` （起始于第 %d 行附近）" % (i - len(buf)),
                        ["补上闭合 ```", "删除该代码块"])
                code = "\n".join(buf)
                cls = ' class="language-%s"' % re.sub(r"[^a-zA-Z0-9_-]", "", lang) if lang else ""
                out.append("<pre%s><code>%s</code></pre>" % (cls, esc(code)))
                i += 1
                continue

            # 空行
            if not s:
                flush_para(); flush_quote(); i += 1; continue

            # 水平线（独立行, 非表格分隔行场景: 上一输出不是表头）
            if re.match(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$", s) and not (out and out[-1].startswith("<table>")):
                flush_para(); flush_quote()
                out.append("<hr/>"); i += 1; continue

            # ATX 标题
            m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", s)
            if m:
                flush_para(); flush_quote()
                lv = len(m.group(1)); txt = m.group(2)
                out.append("<h%d>%s</h%d>" % (lv, self._inline(txt), lv))
                i += 1; continue

            # 表格（首行含 | 且下一行是分隔行）
            if "|" in s and i + 1 < n and re.match(r"^[\s|:\-]+$", lines[i + 1].strip()) and "|" in lines[i + 1]:
                flush_para(); flush_quote()
                tbl = []
                while i < n and "|" in lines[i].strip():
                    tbl.append(lines[i]); i += 1
                html = self._table(tbl)
                if html:
                    out.append(html); continue
                # 分隔行校验失败 -> 回退按普通文本处理
                out.append("<p>%s</p>" % self._inline(" ".join(tbl)))
                continue

            # 引用块（连续 > 行）
            if s.startswith(">"):
                flush_para()
                quote.append(s.lstrip("> ").strip())
                i += 1; continue

            # 列表（有序/无序, 支持两层嵌套）
            lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", raw)
            if lm:
                flush_para(); flush_quote()
                i = self._list(lines, i, out)
                continue

            # 普通段落行
            para.append(s)
            i += 1

        flush_para(); flush_quote()
        return "\n".join(out), self.images

    def _list(self, lines, i, out):
        """从 lines[i] 起消费一段列表, 返回新的 i。支持 2 层嵌套。"""
        stack = []  # [(indent, tag)]
        def close_to(depth):
            while len(stack) > depth:
                _, tag = stack.pop()
                out.append("</li></%s>" % tag)
            if stack:
                out.append("</li>")

        while i < len(lines):
            raw = lines[i]
            if not raw.strip():
                break
            lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", raw)
            if not lm:
                break
            indent, marker, content = len(lm.group(1).replace("\t", "  ")), lm.group(2), lm.group(3)
            tag = "ul" if marker in "-*+" else "ol"
            depth = indent // 2 + 1

            if len(stack) < depth:
                while len(stack) < depth:
                    out.append("<%s>" % tag)
                    stack.append((indent, tag))
            else:
                close_to(depth - 1) if depth <= len(stack) else None
                if stack and stack[-1][1] != tag:
                    _, old = stack.pop()
                    out.append("</li></%s>" % old)
                    out.append("<%s>" % tag)
                    stack.append((indent, tag))
                if stack:
                    out.append("</li>")
            out.append("<li>%s" % self._inline(content))
            i += 1
        close_to(0)
        return i


# ------------------------------------------------------- 解析为章节/IR ----

def split_chapters(md_text):
    """按 H1 切章; 无 H1 时整篇单章。返回 (meta_front, chapters:list[dict])。"""
    front = {}
    text = md_text
    if text.lstrip().startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
        if m:
            for line in m.group(1).split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    front[k.strip().lower()] = v.strip().strip('"').strip("'")
            text = text[m.end():]

    # 代码块占位, 防止代码内的 # 被当标题
    fences = []
    def stash(m):
        fences.append(m.group(0))
        return "\n@@FENCE_%d@@\n" % (len(fences) - 1)
    text_nc = re.sub(r"```[^\n]*\n.*?(?:```|$)", stash, text, flags=re.S)

    h1s = list(re.finditer(r"^# (.+?)\s*$", text_nc, re.M))
    chapters = []
    if not h1s:
        if text_nc.strip():
            chapters.append({"title": None, "start": 0, "end": len(text_nc)})
    else:
        for idx, m in enumerate(h1s):
            end = h1s[idx + 1].start() if idx + 1 < len(h1s) else len(text_nc)
            chapters.append({"title": m.group(1).strip(), "start": m.end(), "end": end})
        # 第一个 H1 之前的引言内容并进第一章
        if h1s[0].start() > 0 and text_nc[:h1s[0].start()].strip():
            chapters[0]["start"] = 0

    out = []
    for idx, ch in enumerate(chapters, 1):
        seg = text_nc[ch["start"]:ch["end"]]
        seg = re.sub(r"@@FENCE_(\d+)@@", lambda m: fences[int(m.group(1))], seg)
        out.append({"no": idx, "title": ch["title"], "md": seg.strip()})
    return front, out


def build_ir(md_text, opt):
    """Markdown 全文 -> JSON IR（中间表示）。全部决策在此完成, forge 不再有解析逻辑。"""
    front, raw_chapters = split_chapters(md_text)
    conv = Md2Xhtml()

    meta_title = opt.get("title") or front.get("title")
    meta_author = opt.get("author") or front.get("author")
    meta_lang = opt.get("lang") or front.get("language") or ""
    meta_date = front.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not raw_chapters:
        raise ForgeError("E_INPUT_EMPTY", "输入 Markdown",
                         "文件为空或只有 frontmatter, 没有任何正文内容",
                         ["检查文件路径是否正确", "确认文件里有正文内容"])
    # 无标题章节 -> 取首个 H2 作章名, 再无则编序
    for ch in raw_chapters:
        if not ch["title"]:
            h2 = re.search(r"^## (.+?)\s*$", ch["md"], re.M)
            ch["title"] = h2.group(1).strip() if h2 else "第 %d 章" % ch["no"]

    if not meta_title:
        meta_title = raw_chapters[0]["title"]
    if not meta_author:
        meta_author = "Unknown Author"

    full_text = md_text
    language = meta_lang or ("zh-CN" if is_cjk_dominant(full_text) else "en")

    chapters_ir = []
    seen_anchor = set()
    for ch in raw_chapters:
        html, _ = conv.blocks(ch["md"])
        anchor = "chap-%03d" % ch["no"]
        sections = []
        for m in re.finditer(r"^(#{2,3}) (.+?)\s*$", ch["md"], re.M):
            lv, st = len(m.group(1)), m.group(2).strip()
            a = slugify(st, "sec-%d-%d" % (ch["no"], len(sections) + 1))
            while a in seen_anchor:
                a += "-x"
            seen_anchor.add(a)
            sections.append({"level": lv, "title": st, "anchor": a})
        chapters_ir.append({
            "no": ch["no"], "title": ch["title"], "anchor": anchor,
            "sections": sections, "html": html,
        })

    css_variant = "zh" if language.startswith("zh") else "en"
    ir = {
        "ir_version": 1,
        "generator": "md-to-epub %s" % SKILL_VERSION,
        "meta": {
            "title": meta_title,
            "author": meta_author,
            "language": language,
            "date": meta_date,
            "identifier": "urn:uuid:%s" % str(uuid.uuid4()),
            "css_variant": css_variant,
        },
        "chapters": chapters_ir,
        "images": [{"abs": p, "media_type": mt, "href": h} for p, mt, h in conv.images],
    }
    if not meta_title.strip():
        raise ForgeError("E_META_MISSING", "书名 title",
                         "frontmatter 与命令行均未提供书名, 且正文无可用 H1/H2",
                         ["在 frontmatter 加 title:", "或用 --title 参数传入"])
    return ir


# ------------------------------------------------------------ CSS ----

CSS_EN = """body{font-family:Georgia,'Times New Roman',serif;line-height:1.6;margin:0;padding:1em;}
h1{font-size:1.35em;margin:1.6em 0 .8em;page-break-before:always;}
h1:first-child{page-break-before:avoid;}
h2{font-size:1.18em;margin:1.3em 0 .5em;color:#1a3a5c;}
h3{font-size:1.05em;margin:1.1em 0 .5em;}
p{margin:.7em 0;text-align:justify;}
a{color:#0b57a4;text-decoration:none;}
code,pre{font-family:'SF Mono',Consolas,'Courier New',monospace;font-size:.78em;}
code{background:#f4f4f4;padding:.1em .35em;border:1px solid #e2e2e2;border-radius:3px;}
pre{background:#f8f8f8;border:1px solid #e0e0e0;border-left:3px solid #0b57a4;padding:.8em;overflow-x:auto;line-height:1.35;}
pre code{background:none;border:none;padding:0;font-size:1em;}
blockquote{border-left:4px solid #0b57a4;margin:1em 0;padding:.2em 1em;color:#444;font-style:italic;}
table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:.9em;}
th{background:#0b57a4;color:#fff;padding:.55em .8em;text-align:left;border:1px solid #0b57a4;}
td{padding:.5em .8em;border:1px solid #ddd;}
tbody tr:nth-child(even){background:#f6f8fa;}
img{max-width:100%;}
hr{border:none;border-top:2px solid #ddd;margin:2em 0;}
"""

CSS_ZH = """body{font-family:'Source Han Serif SC','Noto Serif CJK SC','Songti SC',SimSun,serif;line-height:1.8;margin:0;padding:1em;}
h1,h2,h3,h4,h5,h6{font-family:'Source Han Sans SC','Noto Sans CJK SC','PingFang SC','Microsoft YaHei',sans-serif;}
h1{font-size:1.35em;margin:1.6em 0 .9em;page-break-before:always;}
h1:first-child{page-break-before:avoid;}
h2{font-size:1.2em;margin:1.4em 0 .6em;color:#16324f;}
h3{font-size:1.08em;margin:1.1em 0 .5em;}
p{margin:.6em 0;text-align:justify;text-indent:2em;}
h1+p,h2+p,h3+p,blockquote p,li p,td p{ text-indent:0; }
a{color:#0b57a4;text-decoration:none;}
code,pre{font-family:Consolas,'Courier New',monospace;font-size:.8em;}
code{background:#f4f4f4;padding:.1em .35em;border:1px solid #e2e2e2;border-radius:3px;}
pre{background:#f8f8f8;border:1px solid #e0e0e0;border-left:3px solid #0b57a4;padding:.8em;overflow-x:auto;line-height:1.4;}
pre code{background:none;border:none;padding:0;font-size:1em;}
blockquote{border-left:4px solid #0b57a4;margin:1em 0;padding:.3em 1em;color:#444;}
blockquote p{text-indent:0;}
table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:.9em;}
th{background:#16324f;color:#fff;padding:.55em .8em;text-align:left;border:1px solid #16324f;}
td{padding:.5em .8em;border:1px solid #ddd;}
tbody tr:nth-child(even){background:#f6f8fa;}
img{max-width:100%;}
hr{border:none;border-top:2px solid #ddd;margin:2em 0;}
"""

# ------------------------------------------------- EPUB3 打包（纯 zipfile）----

XHTML_TP = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}">
<head><title>{title}</title><link rel="stylesheet" type="text/css" href="style/main.css"/></head>
<body>
{body}
</body>
</html>
"""

COVER_XHTML_TP = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}">
<head><title>Cover</title></head>
<body style="margin:0;text-align:center;">
<div><img src="images/{cover_name}" alt="cover" style="max-width:100%;max-height:100%;"/></div>
</body>
</html>
"""


def _sec_links_xml(ch):
    if not ch["sections"]:
        return ""
    items = "".join(
        '<li><a href="%s.xhtml#%s">%s</a></li>' % (ch["anchor"], s["anchor"], esc(s["title"]))
        for s in ch["sections"])
    return "<ol>%s</ol>" % items


def forge_epub(ir, out_path, cover=None, custom_css=None):
    """IR -> EPUB3。原子写 + 自校验 + last-good。"""
    meta, chapters = ir["meta"], ir["chapters"]
    lang, title = meta["language"], meta["title"]
    css = open(custom_css, "r", encoding="utf-8").read() if custom_css else (CSS_ZH if meta.get("css_variant") == "zh" else CSS_EN)

    # 校验 IR
    if not isinstance(ir.get("chapters"), list) or not chapters:
        raise ForgeError("E_IR_INVALID", "IR.chapters", "IR 缺少非空 chapters 数组",
                         ["用 parse 子命令从 Markdown 重新生成 IR"])
    if not meta.get("title", "").strip():
        raise ForgeError("E_META_MISSING", "IR.meta.title", "书名为空",
                         ["编辑 IR 的 meta.title 后重试"])
    if cover and not os.path.isfile(cover):
        raise ForgeError("E_COVER_MISSING", cover, "封面文件不存在: %s" % os.path.abspath(cover),
                         ["确认封面路径", "去掉 --cover 参数"])
    cover_ext = os.path.splitext(cover)[1].lower() if cover else ""
    if cover and cover_ext not in IMAGE_EXT_MAP:
        raise ForgeError("E_IMAGE_UNSUPPORTED", cover,
                         "封面格式 %s 不支持（支持 %s）" % (cover_ext, ", ".join(sorted(IMAGE_EXT_MAP))),
                         ["转成 png/jpg 后重试"])

    has_cover = bool(cover)
    cover_name = os.path.basename(cover) if has_cover else ""
    cover_mt = IMAGE_EXT_MAP.get(cover_ext, "image/png") if has_cover else ""

    nav_lis = []
    if has_cover:
        nav_lis.append('<li><a href="cover.xhtml">封面 Cover</a></li>')
    for ch in chapters:
        nav_lis.append('<li><a href="%s.xhtml">%s</a>%s</li>' % (ch["anchor"], esc(ch["title"]), _sec_links_xml(ch)))
    nav_body = "<ol>" + "".join(nav_lis) + "</ol>"

    nav_xhtml = XHTML_TP.format(lang=lang, title="TOC", body=nav_body)

    ncx_points = []
    play = 1
    if has_cover:
        ncx_points.append('<navPoint id="navPoint-%d" playOrder="%d"><navLabel><text>Cover</text></navLabel><content src="cover.xhtml"/></navPoint>' % (play, play)); play += 1
    for ch in chapters:
        ncx_points.append('<navPoint id="navPoint-%d" playOrder="%d"><navLabel><text>%s</text></navLabel><content src="%s.xhtml"/></navPoint>' % (play, play, esc(ch["title"]), ch["anchor"])); play += 1
        for s in ch["sections"]:
            ncx_points.append('<navPoint id="navPoint-%d" playOrder="%d"><navLabel><text>%s</text></navLabel><content src="%s.xhtml#%s"/></navPoint>' % (play, play, esc(s["title"]), ch["anchor"], s["anchor"])); play += 1
    ncx_xml = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
               '<head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="1"/>'
               '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
               '<docTitle><text>%s</text></docTitle>\n'
               '<navMap>%s</navMap>\n</ncx>\n') % (esc(meta["identifier"]), esc(title), "".join(ncx_points))

    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style/main.css" media-type="text/css"/>']
    if has_cover:
        manifest.append('<item id="cover-image" href="images/%s" media-type="%s" properties="cover-image"/>' % (cover_name, cover_mt))
        manifest.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
    for img in ir.get("images", []):
        manifest.append('<item id="img-%s" href="%s" media-type="%s"/>' % (
            os.path.splitext(os.path.basename(img["href"]))[0], img["href"], img["media_type"]))
    for ch in chapters:
        manifest.append('<item id="%s" href="%s.xhtml" media-type="application/xhtml+xml"/>' % (ch["anchor"], ch["anchor"]))

    spine = []
    if has_cover:
        spine.append('<itemref idref="cover"/>')
    for ch in chapters:
        spine.append('<itemref idref="%s"/>' % ch["anchor"])

    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="%s">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           '<dc:identifier id="bookid">%s</dc:identifier>\n'
           '<dc:title>%s</dc:title>\n'
           '<dc:language>%s</dc:language>\n'
           '<dc:creator>%s</dc:creator>\n'
           '<dc:date>%s</dc:date>\n'
           '<meta property="dcterms:modified">%s</meta>\n'
           '<meta name="cover" content="cover-image"/>\n'
           '</metadata>\n'
           '<manifest>%s</manifest>\n'
           '<spine toc="ncx">%s</spine>\n'
           '</package>\n') % (lang, esc(meta["identifier"]), esc(title), esc(lang), esc(meta["author"]),
                              esc(meta.get("date", modified[:10])), modified,
                              "".join(manifest), "".join(spine))

    # ---- 原子写: 临时文件 -> 自校验 -> rename ----
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".epub.tmp", dir=out_dir)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml",
                       '<?xml version="1.0" encoding="utf-8"?>\n'
                       '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                       '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
                       '</container>\n')
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/nav.xhtml", nav_xhtml)
            z.writestr("OEBPS/toc.ncx", ncx_xml)
            z.writestr("OEBPS/style/main.css", css)
            if has_cover:
                z.writestr("OEBPS/cover.xhtml", COVER_XHTML_TP.format(lang=lang, cover_name=cover_name))
                with open(cover, "rb") as f:
                    z.write(cover, "OEBPS/images/%s" % cover_name)
            for img in ir.get("images", []):
                z.write(img["abs"], "OEBPS/" + img["href"])
            for ch in chapters:
                z.writestr("OEBPS/%s.xhtml" % ch["anchor"],
                           XHTML_TP.format(lang=lang, title=esc(ch["title"]), body=ch["html"]))
        # 自校验
        problems = validate_epub(tmp_path, expect_chapters=len(chapters))
        if problems:
            raise ForgeError("E_VALIDATE_FAILED", out_path, "; ".join(problems),
                             ["重跑一次（若复现请带 IR 文件反馈）"])
        os.replace(tmp_path, out_path)   # last-good 原子替换
    except ForgeError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise ForgeError("E_WRITE_FAILED", out_path,
                         "%s: %s" % (type(e).__name__, e),
                         ["确认输出目录可写", "确认磁盘有剩余空间"])
    return {"chapters": len(chapters), "images": len(ir.get("images", [])),
            "cover": has_cover, "css": "zh" if meta.get("css_variant") == "zh" else "en"}


def validate_epub(path, expect_chapters=0):
    """EPUB 结构自校验; 返回问题列表（空 = 通过）。"""
    problems = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if not names or names[0] != "mimetype":
                problems.append("mimetype 不是第一个条目")
            else:
                info = z.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    problems.append("mimetype 被压缩（必须 STORED）")
                if z.read("mimetype") != b"application/epub+zip":
                    problems.append("mimetype 内容错误")
            for req in ["META-INF/container.xml", "OEBPS/content.opf",
                        "OEBPS/nav.xhtml", "OEBPS/toc.ncx", "OEBPS/style/main.css"]:
                if req not in names:
                    problems.append("缺少 %s" % req)
            xhtml_count = sum(1 for x in names if x.startswith("OEBPS/chap-"))
            if xhtml_count != expect_chapters:
                problems.append("章节数不匹配: 文件 %d / IR %d" % (xhtml_count, expect_chapters))
            bad = z.testzip()
            if bad:
                problems.append("zip 损坏条目: %s" % bad)
    except zipfile.BadZipFile as e:
        problems.append("不是有效 zip: %s" % e)
    return problems


# ------------------------------------------------------------ CLI ----

def _read_md(path):
    if not os.path.isfile(path):
        raise ForgeError("E_INPUT_MISSING", path, "文件不存在: %s" % os.path.abspath(path),
                         ["检查路径拼写（注意中文路径）", "确认文件已被保存"])
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def main(argv=None):
    p = argparse.ArgumentParser(prog="epub_forge", description="Markdown -> EPUB3 (zero-dependency)")
    p.add_argument("mode", choices=["all", "parse", "forge"])
    p.add_argument("input")
    p.add_argument("-o", "--output", help="输出 .epub 路径（mode=all/forge）")
    p.add_argument("--ir", help="IR JSON 落盘路径（mode=parse/all）")
    p.add_argument("--title"); p.add_argument("--author"); p.add_argument("--lang")
    p.add_argument("--cover", help="封面图片路径")
    p.add_argument("--css", help="自定义 CSS 文件（覆盖内置中/英样式）")
    args = p.parse_args(argv)

    try:
        t0 = datetime.now()
        if args.mode in ("all", "parse"):
            md = _read_md(args.input)
            opt = {"title": args.title, "author": args.author, "lang": args.lang}
            ir = build_ir(md, opt)
            ir_path = args.ir or (os.path.splitext(args.output or args.input)[0] + ".ir.json")
            os.makedirs(os.path.dirname(os.path.abspath(ir_path)) or ".", exist_ok=True)
            with open(ir_path, "w", encoding="utf-8") as f:
                json.dump(ir, f, ensure_ascii=False, indent=1)
            if args.mode == "parse":
                emit({"ok": True, "mode": "parse", "ir": os.path.abspath(ir_path),
                      "chapters": len(ir["chapters"]), "images": len(ir["images"]),
                      "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000)})
                return 0
            ir_obj, ir_path_used = ir, ir_path
        else:  # forge
            with open(args.input, "r", encoding="utf-8") as f:
                ir_obj = json.load(f)
            ir_path_used = args.input

        if not args.output:
            raise ForgeError("E_OUTPUT_MISSING", "--output", "mode=all/forge 需要 -o 指定输出 .epub 路径",
                             ["补上 -o /path/to/book.epub"])
        stats = forge_epub(ir_obj, args.output, cover=args.cover, custom_css=args.css)
        emit({"ok": True, "mode": args.mode, "output": os.path.abspath(args.output),
              "ir": os.path.abspath(ir_path_used), "validated": True, **stats,
              "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000)})
        return 0
    except ForgeError as e:
        emit(e.payload); return 1
    except Exception as e:
        emit({"error": {"code": "E_UNEXPECTED", "subject": type(e).__name__,
                        "evidence": str(e)[:300], "fixes": ["带完整回执反馈"]}})
        return 1


if __name__ == "__main__":
    sys.exit(main())
