# Anytype → Markdown Import Preprocessors

[![Python](https://img.shields.io/badge/python-3.7%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)]()

将 [Anytype](https://anytype.io/) 导出的 Markdown 文件转换为 **Obsidian** 或 **Notion** 兼容格式并打包为 ZIP，一键导入。

---

## 🚀 Quick Start

**导出到 Obsidian？** → 运行 [`obsidian_import_preprocessor.py`](#-obsidian-import-preprocessor)

**导出到 Notion？** → 运行 [`notion_import_preprocessor_v3.3.py`](#-notion-import-preprocessor)

两个脚本均自动扫描同级目录下所有 `Anytype.*` 文件夹，每个独立处理。

---

## 📦 Obsidian Import Preprocessor

> 保留 Markdown 原始样貌 — 解压即 Vault，零配置导入。

### 与 Notion 版的关键区别

| 特性 | Obsidian 版 | Notion 版 |
|------|:-----------:|:---------:|
| YAML frontmatter | ✅ 保留 | ❌ 移除 |
| Markdown 行内样式 | ✅ 保留 | ❌ 剥离（`**粗体**` → 纯文本） |
| 内部链接 | `[[wikilinks]]` | `[text](page.md)` |
| 图库/反向链接支持 | ✅ 原生 | ❌ 不支持 |
| 图片 | 嵌入 + 打包 | 嵌入 + 打包 |
| 视频/音频/PDF | `![[file]]` 内嵌 + 打包 | 文字路径（手动上传） |
| 附件 (.zip/.c/.msi 等) | `[file.ext](path)` 链接 + 打包 | 文字路径（手动上传） |
| 代码块 | 原样保留 | 原样保留 |

### 处理管线

```
  fix_markdown_escapes      转义残留还原（\_ → _, \* → *）
        ↓
  fix_path_separators       链接路径 \ → /
        ↓
  process_images            清理图片 alt text
        ↓
  fix_table_br_tags         移除 <br> / 删除空行 / 规范化列分隔
        ↓
  clean_trailing_whitespace 去尾部空白 / 合并连续空行
        ↓
  fix_table_spacing         确保表格前后空行（GFM 规范）
        ↓
  convert_links_to_wikilinks Markdown 链接 → [[wikilinks]]
```

### 输出 ZIP 结构

```
obsidian_import_<Vault名>.zip
├── 主页.md
├── 子页面A.md
├── 子页面B.md
├── files/
│   ├── image.png
│   ├── audio.wav
│   ├── document.pdf
│   └── attachment.zip
└── _OBSIDIAN_IMPORT_GUIDE.md
```

### 用法

```bash
python obsidian_import_preprocessor.py
```

解压 ZIP → Obsidian → **Open folder as vault** → 完成。

---

## 📦 Notion Import Preprocessor

> v3.3 — 专为 Notion 的 Markdown 解析器裁剪，解决样式残留与兼容问题。

### v3.3 关键修复

- **转义残留修复** — 仅处理 `\_` → `_`、`\*` → `*`、`****` → `**` 三类明确 artifact，不触碰 `\-` `\|` `\#` 等（保护 LaTeX 公式和代码内容）
- **表格样式剥离** — 增强 `_strip_cell_formatting()`，移除单元格内 `**粗体**`、`` `代码` ``、`_斜体_` 等 Notion 不支持的样式
- **表格前空行** — 确保每个表格前有空行，避免 Notion 将表格前置文本误并入表格

### 处理管线

```
  remove_yaml_frontmatter   移除 frontmatter（Notion 不支持）
        ↓
  fix_markdown_escapes      转义残留还原
        ↓
  update_internal_links     内部链接路径更新（配合 H1 重命名）
        ↓
  fix_path_separators       链接路径 \ → /
        ↓
  fix_windows_paths         正文中裸路径 \ → /
        ↓
  process_images            图片 alt 清理 + 路径规范化
        ↓
  process_attachments       非图片附件 → 📎 路径文本
        ↓
  fix_table_br_tags         移除表格 <br>
        ↓
  strip_table_formatting    剥离表格内 Markdown 样式
        ↓
  ensure_blank_line_before_tables  表格前补空行
        ↓
  clean_trailing_whitespace 去尾部空白
```

### 输出 ZIP 结构

```
notion_import_<主标题>.zip
├── 主页.md
├── 子页面A.md
├── 子页面B.md
├── files/
│   └── image.png            （仅被引用的图片）
└── README_IMPORT_GUIDE.txt
```

### 用法

```bash
python notion_import_preprocessor_v3.3.py
```

ZIP → Notion → **Import → Text & Markdown** → 完成。

---

## 📊 功能对比矩阵

| | Obsidian | Notion v3.3 |
|---|---|---|
| **定位** | 解压即 Vault | 导入用 ZIP |
| **YAML 处理** | 保留 | 移除 |
| **行内样式** | 全部保留 | 剥离（表格内） |
| **内部链接** | `[[wikilinks]]` | `[text](page.md)` |
| **图片** | 嵌入 + 打包 | 嵌入 + 打包 |
| **视频/音频** | 内嵌 + 打包 | 📎 路径文本 |
| **附件** | 链接 + 打包 | 📎 路径文本 |
| **表格** | GFM 规范修复 | 样式剥离 + 空行 |
| **LaTeX** | 保留 | 保护转义 |
| **代码块** | 保留 | 保留 |
| **外部依赖** | 0 | 0 |

---

## 📋 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| **obsidian v1.1** | 2026-06-08 | 修复表格 GFM 空行、附件 wikilink、扩展名显示 |
| obsidian v1.0 | 2026-06-05 | 初始 Obsidian 版本 |
| notion v3.3 | 2026-06-05 | 转义残留修复、表格样式剥离增强 |
| notion v3.0 | 2026-06-04 | 表格 HTML 剥离、多目录支持 |
| notion v2.0 | 2026-06-03 | 智能文件重命名、H1 标题提取 |
| notion v1.0 | 2026-06-02 | 初始 Notion 版本 |

---

## ⚙️ Requirements

- **Python 3.7+** — 零外部依赖，标准库 `zipfile` + `re` + `pathlib`
- Windows / macOS / Linux

## 📄 License

MIT
