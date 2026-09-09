#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_forge.py — md-to-epub 自检（零依赖，python test_forge.py 即跑）"""
import json, os, struct, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "scripts", "epub_forge.py")
PY = sys.executable
TMP = tempfile.mkdtemp(prefix="m2e_test_")
results = []


def run(args, cwd=TMP):
    r = subprocess.run([PY, ENGINE] + args, capture_output=True, text=True,
                       encoding="utf-8", cwd=cwd)
    try:
        return r.returncode, json.loads(r.stdout)
    except Exception:
        return r.returncode, {"_raw": r.stdout, "_err": r.stderr}


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail and not cond else ""))


# ---------- fixture: 多章 md + 本地图片 ----------
def tiny_png(path):
    # 1x1 红色 png（纯手工构造，不引第三方库）
    import zlib
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


img = os.path.join(TMP, "pic.png")
tiny_png(img)
md = os.path.join(TMP, "book.md")
with open(md, "w", encoding="utf-8") as f:
    f.write("""---
title: 测试之书
author: 测试员
---

## 前言

第一章之前的引言内容，应并入第一章。

# 第一章 基础

中文正文段落，验证首行缩进与**粗体**、*斜体*、`行内码`、[链接](https://example.com)。

![测试图](pic.png)

## 小节甲

- 项目一
- 项目二
  - 嵌套子项
1. 有序一
2. 有序二

> 引用第一行
> 引用第二行

# 第二章 数据

| 律 | 问题 |
|---|---|
| 有籍 | 谁干的 |
| 有证 | 拿什么证明 |

```python
print("# 不是标题")
```
""")

# ---------- T1: all 一键成书 ----------
out = os.path.join(TMP, "book.epub")
code, rec = run(["all", md, "-o", out])
check("T1 all 成功", code == 0 and rec.get("ok"), json.dumps(rec, ensure_ascii=False))
check("T1 中文样式自动识别", rec.get("css") == "zh")
check("T1 两个章节", rec.get("chapters") == 2)
check("T1 图片嵌入 1 张", rec.get("images") == 1)
check("T1 结构自校验通过", rec.get("validated") is True)

# ---------- T2: EPUB 内容抽检 ----------
with zipfile.ZipFile(out) as z:
    names = z.namelist()
    check("T2 mimetype 首条且未压缩", names[0] == "mimetype" and z.getinfo("mimetype").compress_type == 0)
    opf = z.read("OEBPS/content.opf").decode("utf-8")
    check("T2 opf 含书名/作者", "测试之书" in opf and "测试员" in opf)
    check("T2 opf 声明 cover 属性无误(无封面)", 'properties="cover-image"' not in opf)
    nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    check("T2 nav 含两章锚点", "chap-001.xhtml" in nav and "chap-002.xhtml" in nav)
    c1 = z.read("OEBPS/chap-001.xhtml").decode("utf-8")
    check("T2 前言并入第一章", "应并入第一章" in c1)
    check("T2 图片重写为内部引用", 'src="images/img_001.png"' in c1)
    check("T2 嵌套列表渲染 ul>li", "<ul>" in c1 and "<ol>" in c1)
    check("T2 引用块合并", c1.count("<blockquote>") == 1 and "引用第二行" in c1)
    c2 = z.read("OEBPS/chap-002.xhtml").decode("utf-8")
    check("T2 表格渲染", "<table>" in c2 and "<th>" in c2)
    check("T2 代码块内 # 未被当标题", "print(&quot;# 不是标题&quot;)" in c2 and c2.count("<h1>") == 0)
    check("T2 章节含样式链接", 'href="style/main.css"' in c1)

# ---------- T3: parse/forge 两段管线 ----------
ir_path = os.path.join(TMP, "book.ir.json")
code, rec = run(["parse", md, "--ir", ir_path])
check("T3 parse 出 IR", code == 0 and rec.get("ok") and os.path.isfile(ir_path))
with open(ir_path, encoding="utf-8") as f:
    ir = json.load(f)
check("T3 IR 含 meta+chapters", ir.get("meta", {}).get("title") == "测试之书" and len(ir["chapters"]) == 2)
ir["meta"]["title"] = "改名之书"
ir2 = os.path.join(TMP, "book2.ir.json")
with open(ir2, "w", encoding="utf-8") as f:
    json.dump(ir, f, ensure_ascii=False)
out2 = os.path.join(TMP, "book2.epub")
code, rec = run(["forge", ir2, "-o", out2])
check("T3 forge 从 IR 出书", code == 0 and rec.get("ok"))
with zipfile.ZipFile(out2) as z:
    check("T3 IR 改名生效", "改名之书" in z.read("OEBPS/content.opf").decode("utf-8"))

# ---------- T4: 错误回执（规则码） ----------
code, rec = run(["all", os.path.join(TMP, "nope.md"), "-o", out])
check("T4 E_INPUT_MISSING", rec.get("error", {}).get("code") == "E_INPUT_MISSING")
bad = os.path.join(TMP, "bad.md")
with open(bad, "w", encoding="utf-8") as f:
    f.write("# 章\n\n```python\n没闭合\n")
code, rec = run(["all", bad, "-o", out])
check("T4 E_CODE_UNCLOSED", rec.get("error", {}).get("code") == "E_CODE_UNCLOSED")
badimg = os.path.join(TMP, "badimg.md")
with open(badimg, "w", encoding="utf-8") as f:
    f.write("# 章\n\n![缺图](ghost.png)\n")
code, rec = run(["all", badimg, "-o", out])
check("T4 E_IMAGE_MISSING", rec.get("error", {}).get("code") == "E_IMAGE_MISSING")
code, rec = run(["forge", ir2])
check("T4 E_OUTPUT_MISSING", rec.get("error", {}).get("code") == "E_OUTPUT_MISSING")

# ---------- 汇总 ----------
fails = [r for r in results if not r[1]]
print("\n== %d/%d passed ==" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
