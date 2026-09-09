# Changelog

## v1.0.0 (2026-09-08)

首个公开版本。衍生自 smerchek/claude-epub-skill (MIT)，编译引擎完全重写：

- 纯 Python 标准库零依赖（zipfile 打包 EPUB3，替换 ebooklib/markdown2/Pygments）
- JSON IR 中间层：`parse`（md→IR）与 `forge`（IR→EPUB）两段可查管线（确定性输出层）
- 中文自动排版：语言自动检测（中文占比>30%→zh-CN），宋体/黑体字体栈、首行缩进 2em、1.8 行高
- 本地图片真实嵌入（png/jpg/gif/svg/webp，自动收集+重写+mediatype）——上游 README 声称支持但代码未实现
- 原子写 + zip 结构自校验 + last-good 替换
- JSON 修复回执（12 个稳定规则码）替代裸堆栈
- `--cover` 封面进 manifest（properties="cover-image"）
- 24 项零依赖自检（test_forge.py）全过
