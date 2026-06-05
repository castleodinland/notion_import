#!/usr/bin/env python3
"""
Obsidian 导入预处理脚本 v1.0
将 Anytype 导出的一组互链 Markdown 文件转换为 Obsidian 兼容格式，并打包为 ZIP。

与 Notion 版本的关键区别：
  - 保留 YAML frontmatter（Obsidian 支持 properties/tags）
  - 保留所有 Markdown 行内样式（粗体、斜体、代码等），Obsidian 原生支持
  - 内部链接转换为 [[wikilinks]] 格式（支持图谱/反向链接/自动重命名）
  - 包含所有媒体文件：图片、视频、音频、PDF、附件等
  - ZIP 解压后直接作为 Obsidian vault 打开，无需任何转换

用法：
    python obsidian_import_preprocessor.py
"""

import os
import re
import sys
import io
import zipfile
import pathlib
import tempfile
from datetime import datetime

# ═══════════════════════════════════════════════════════════
# 输出编码设置
# ═══════════════════════════════════════════════════════════
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ═══════════════════════════════════════════════════════════
# 配置区
# ═══════════════════════════════════════════════════════════

WORK_DIR = pathlib.Path(__file__).parent

# 资源目录名（相对于每个 Anytype 工作目录，也是 ZIP 内的子目录）
ASSETS_DIR = "files"

# 图片扩展名
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff", ".tif"}

# 视频扩展名
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv"}

# 音频扩展名
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma", ".opus"}

# Obsidian 支持的嵌入附件格式（图片/视频/音频/PDF 可直接嵌入页面）
EMBEDDABLE_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | {".pdf"}

# 所有支持的附件扩展名
ALL_ASSET_EXTENSIONS = EMBEDDABLE_EXTENSIONS | {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".py", ".js", ".ts", ".html", ".css", ".c", ".cpp", ".h", ".hpp",
    ".txt", ".log", ".md", ".markdown",
    ".bin", ".hex", ".elf", ".map", ".axf", ".out",
    ".drawio", ".vsdx",
    ".ttf", ".otf", ".woff", ".woff2",
    ".fbx", ".obj", ".gltf", ".glb",
}

# ZIP 内需要排除的文件/目录
EXCLUDE_DIRS = {".workbuddy", "schemas", "__pycache__", ".obsidian"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def discover_all_work_dirs() -> list:
    """自动发现所有 Anytype.* 子目录。"""
    dirs = []
    for subdir in sorted(WORK_DIR.iterdir()):
        if subdir.is_dir() and subdir.name.startswith('Anytype'):
            subdir_mds = list(subdir.glob('*.md'))
            if subdir_mds:
                dirs.append(subdir)
    return dirs


def get_extension(filepath: str) -> str:
    return os.path.splitext(filepath)[1].lower()


def get_file_size_info(filepath: pathlib.Path) -> str:
    if not filepath.exists():
        return "未知大小"
    size_bytes = filepath.stat().st_size
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def sanitize_filename(title: str) -> str:
    """将 H1 标题转为安全的文件名。"""
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)
    safe = safe.strip('. ')
    safe = re.sub(r'_+', '_', safe)
    return safe


def remove_yaml_frontmatter(content: str) -> str:
    """去除 YAML frontmatter（仅用于标题提取，不修改原始内容）。"""
    pattern = r'^---\s*\n.*?\n---\s*\n'
    return re.sub(pattern, '', content, count=1, flags=re.DOTALL)


def extract_h1_title(content: str) -> str:
    """提取 Markdown 的第一个 H1 标题（跳过 YAML frontmatter）。"""
    content_clean = remove_yaml_frontmatter(content)
    match = re.search(r'^#\s+(.+?)\s*$', content_clean, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


# ═══════════════════════════════════════════════════════════
# Markdown 处理函数
# ═══════════════════════════════════════════════════════════

def fix_markdown_escapes(content: str) -> str:
    """
    修复 Anytype 导出时的 Markdown 转义残留（仅在代码块外处理）。

    Obsidian 使用 CommonMark 解析器，支持标准反斜杠转义，因此：
    - 代码块内的转义序列保留原样（它们就是字面量内容）
    - 代码块外的转义序列还原为字面量字符，因为 Anytype 导出器
      将本应是格式标记或普通文本的字符进行了不必要的转义

    处理：
    1. \\_ → _    转义下划线 → 字面量下划线
    2. \\* → *    转义星号 → 字面量星号
    3. \\` → `    转义反引号 → 字面量反引号
    4. ****text**** → **text**  四星号 artifact
    """
    lines = content.split('\n')
    result_lines = []
    in_code_block = False

    for line in lines:
        stripped = line.lstrip()  # 用 lstrip 检测 ``` 前缀（允许缩进）
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue

        if in_code_block:
            result_lines.append(line)
            continue

        # 代码块外：修复转义残留
        line = line.replace('\\_', '_')
        line = line.replace('\\*', '*')
        line = line.replace('\\`', '`')
        line = re.sub(r'\*\*\*\*(.+?)\*\*\*\*', r'**\1**', line)
        line = re.sub(r'\*\*\*\*(.+?)\*\*', r'**\1**', line)

        result_lines.append(line)

    return '\n'.join(result_lines)


def fix_path_separators(content: str) -> str:
    """将 Markdown 链接/图片中反斜杠路径转为正斜杠。"""
    pattern = r'(!?\[[^\]]*\]\()([^)]+)(\))'

    def replace_path(match):
        prefix = match.group(1)
        path = match.group(2)
        suffix = match.group(3)
        fixed_path = path.replace('\\', '/')
        return f"{prefix}{fixed_path}{suffix}"

    return re.sub(pattern, replace_path, content)


def fix_table_br_tags(content: str) -> str:
    """
    移除表格单元格中的 <br> HTML 标签，并删除全空数据行和表格内部空行。
    Anytype 导出的表格每行末尾有 ' <br>'，且常含空行，这些会
    中断 Obsidian 的表格解析，导致表格渲染异常。

    Obsidian/CommonMark 要求表格是一个连续的块，表内不能有空行。
    """
    lines = content.split('\n')
    result = []
    in_code_block = False
    in_table = False  # 跟踪是否在表格块内

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if in_code_block:
            result.append(line)
            continue

        # 检测是否为表格行（含 | 且非代码块边界）
        is_potential_table_line = ('|' in line and not stripped.startswith('```'))

        if is_potential_table_line:
            # 移除 <br> 标签（含前后空白，换为一个空格保持文字间距）
            line = re.sub(r'\s*<br\s*/?>\s*', ' ', line)
            stripped_cleaned = line.strip()

            # 判断是否为表格分隔线（必须含 - 才是真分隔线）
            is_separator = ('-' in stripped_cleaned and
                            bool(re.match(r'^[\s|:\-]+$', stripped_cleaned)))

            if not is_separator:
                # 检测并跳过全空数据行
                cells = [c.strip() for c in stripped_cleaned.split('|')]
                if cells and cells[0] == '':
                    cells.pop(0)
                if cells and cells[-1] == '':
                    cells.pop()
                if all(c == '' for c in cells):
                    continue
                # 规范化列分隔空格：统一为 " | "
                line = re.sub(r'(?<!\\)\s*\|\s*(?!\\)', ' | ', line)
            else:
                # 分隔线同样规范化空格，统一为 " | " 格式
                line = re.sub(r'(?<!\\)\s*\|\s*(?!\\)', ' | ', line)

            line = line.strip()
            in_table = True
        else:
            # 非表格行：如果是紧跟在表格后的空行，跳过（防止在表内插入空行）
            if in_table and stripped == '':
                # 检查下一行是否仍是表格行（管道开头）
                next_is_table = False
                for j in range(i + 1, min(i + 3, len(lines))):
                    next_stripped = lines[j].strip()
                    if next_stripped and '|' in next_stripped:
                        next_is_table = True
                        break
                    elif next_stripped:
                        break
                if next_is_table:
                    # 表内空行，跳过（不写入 result，也不 reset in_table）
                    continue
            in_table = False

        result.append(line)

    return '\n'.join(result)


def normalize_multiline_links(content: str) -> str:
    """
    规范化跨行链接为单行。
    Anytype 导出中链接文本和路径有时分两行：
      [text
      ](url)
    """
    content = re.sub(r'\n(\s*)\]', ']', content)
    return content


def convert_links_to_wikilinks(content: str, file_mapping: dict) -> str:
    """
    将标准 Markdown 内部链接转换为 Obsidian [[wikilinks]] 格式。

    转换规则：
      [text](other.md)        → [[other|text]]
      [text](other.md#锚点)   → [[other#锚点|text]]
      [other](other.md)        → [[other]]
      ![alt](img.png)          → ![alt](img.png)         (图片保持标准格式)
      [text](files/audio.wav)  → [[files/audio.wav|text]] (附件→wikilink)
      [text](https://...)     → [text](https://...)      (外部链接保持原样)

    Obsidian 中标准 Markdown 链接 [text](files/file.ext) 无法正确解析为
    本地文件引用（会尝试作为 URL 打开），必须使用 [[wikilinks]] 格式。
    """
    content = normalize_multiline_links(content)

    # 可嵌入媒体扩展名（使用 ![[file]] 语法可内嵌预览/播放）
    EMBED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | {".pdf"}

    def replace_link(match):
        prefix = match.group(1)   # ! 或空
        link_text = match.group(2)
        link_path = match.group(3)

        # 保留外部链接
        if link_path.startswith('http://') or link_path.startswith('https://'):
            return match.group(0)

        ext = get_extension(link_path).lower()
        basename = os.path.basename(link_path)

        # 图片嵌入保持 Markdown 格式（Obsidian 原生支持）
        if prefix == '!' and ext in IMAGE_EXTENSIONS:
            return match.group(0)

        # 嵌入媒体（视频/音频/PDF）→ ![[files/basename]] 内嵌语法
        if prefix == '!' and ext in EMBED_EXTENSIONS:
            # 保留 files/ 前缀路径
            rel_path = link_path.replace('\\', '/')
            return f"![[{rel_path}]]"

        # 非 .md 文件链接（无 ! 前缀的普通链接）→ 保持标准 Markdown 链接
        # 在 Obsidian 中 [[wikilinks]] 会尝试打开/创建同名笔记，而非打开实际文件。
        # 对于 .docx/.zip/.msi/.c 等非笔记文件，必须使用 [text](path) 格式，
        # 这样 Obsidian 会用系统默认程序打开文件，而非报错。
        # 显示文本始终使用完整文件名（含扩展名），如 main.c 而非 main。
        if ext != '.md' and ext != '':
            rel_path = link_path.replace('\\', '/')
            return f"[{basename}]({rel_path})"

        # .md 内部链接 → wikilink
        # 提取锚点（#heading）
        anchor = ''
        page_name = basename
        if '#' in basename:
            parts = basename.split('#', 1)
            page_name = parts[0]
            anchor = '#' + parts[1]

        # 获取映射后的文件名（去除 .md 扩展名用于 wikilink）
        if basename in file_mapping:
            page_name = file_mapping[basename]
        elif page_name in file_mapping:
            page_name = file_mapping[page_name]

        # wikilink 不需要 .md 扩展名
        page_stem = page_name.replace('.md', '') if page_name.endswith('.md') else page_name

        # 如果链接文本与页面名相同，省略显示文本
        link_stem = link_text.strip()
        if link_stem == page_stem or link_stem == page_name.replace('.md', ''):
            return f"[[{page_stem}{anchor}]]"
        else:
            return f"[[{page_stem}{anchor}|{link_text}]]"

    # 匹配 [text](path) 和 ![text](path)
    pattern = r'(!?)\[([^\]]*?)\]\(([^)]+)\)'
    return re.sub(pattern, replace_link, content)


def process_images(content: str) -> str:
    """清理图片 alt text 多余空格。"""
    def fix_image(match):
        alt = match.group(1).strip()
        path = match.group(2)
        return f'![{alt}]({path})'

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_image, content)


def clean_trailing_whitespace(content: str) -> str:
    """清理每行末尾空格、连续空行、首尾空行。"""
    lines = content.split('\n')
    lines = [line.rstrip() for line in lines]
    while lines and lines[0] == '':
        lines.pop(0)
    cleaned = []
    prev_empty = False
    for line in lines:
        is_empty = (line == '')
        if is_empty and prev_empty:
            continue
        cleaned.append(line)
        prev_empty = is_empty
    while cleaned and cleaned[-1] == '':
        cleaned.pop()
    return '\n'.join(cleaned) + '\n' if cleaned else '\n'


def fix_table_spacing(content: str) -> str:
    """
    确保每个表格块前后有正确的空行间距。

    Obsidian/GFM 要求：
    - 表格前必须有空行（除非紧跟在标题 # 之后）
    - 表格内部不能有空行
    - 表格后应有空行（除非是文档末尾）

    不处理代码块内的内容。
    """
    lines = content.split('\n')
    result = []
    in_code_block = False
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # 代码块保护
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            result.append(lines[i])
            i += 1
            continue

        if in_code_block:
            result.append(lines[i])
            i += 1
            continue

        # 检测是否为表格行
        is_table_line = ('|' in stripped and not stripped.startswith('```'))

        if not is_table_line:
            result.append(lines[i])
            i += 1
            continue

        # 找到完整的表格块（连续的行，忽略表内空行）
        table_start = i
        while i < len(lines):
            s = lines[i].strip()
            if s == '':
                # 检查空行后是否仍是表格行
                next_is_table = False
                for j in range(i + 1, min(i + 3, len(lines))):
                    ns = lines[j].strip()
                    if ns and '|' in ns and not ns.startswith('```'):
                        next_is_table = True
                        break
                    elif ns:
                        break
                if next_is_table:
                    i += 1  # 跳过表内空行
                    continue
                else:
                    break  # 表格结束
            elif '|' in s and not s.startswith('```'):
                i += 1
            else:
                break

        table_lines = lines[table_start:i]

        # 表格前插入空行（如果前面不是空行且不是标题）
        if result:
            prev = result[-1].strip()
            if prev != '' and not prev.startswith('#'):
                result.append('')

        result.extend(table_lines)

        # 表格后插入空行（如果后面不是空行且不是文档末尾）
        if i < len(lines) and lines[i].strip() != '':
            result.append('')

    return '\n'.join(result)


# ═══════════════════════════════════════════════════════════
# 文件映射与链接分析
# ═══════════════════════════════════════════════════════════

def build_file_mapping(md_files: list) -> dict:
    """
    构建 原始文件名 → 新文件名(H1标题) 的映射。
    返回: { "original.md": "H1标题.md", ... }
    """
    mapping = {}
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')
        title = extract_h1_title(content)
        if title:
            new_name = sanitize_filename(title) + '.md'
            mapping[md_file.name] = new_name
        else:
            mapping[md_file.name] = md_file.name
    return mapping


def find_hub_page(md_files: list, file_mapping: dict) -> tuple:
    """
    找到主页面（包含最多内部 .md 链接的页面）。
    返回: (原始文件 pathlib.Path, 新文件名 str)
    """
    max_links = -1
    hub_file = md_files[0]
    hub_name = file_mapping[md_files[0].name]

    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')
        links = re.findall(r'\[([^\]]+)\]\((.+\.md)\)', content)
        if len(links) > max_links:
            max_links = len(links)
            hub_file = md_file
            hub_name = file_mapping[md_file.name]

    return hub_file, hub_name


# ═══════════════════════════════════════════════════════════
# 收集引用资源
# ═══════════════════════════════════════════════════════════

def collect_referenced_assets(processed_files: dict, all_pages: list) -> dict:
    """
    从所有已处理页面中收集被引用的资源文件，按类型分类。
    返回: {
        'referenced': {"image.png", "video.mp4", ...},   # 所有被引用的文件名
        'by_type': {".png": {"a.png"}, ".mp4": {"b.mp4"}, ...}
    }
    """
    referenced = set()
    for page_name in all_pages:
        if page_name not in processed_files:
            continue
        content = processed_files[page_name]
        # 匹配标准 Markdown 链接/图片: [text](path) 和 ![alt](path)
        for match in re.finditer(r'!?\[[^\]]*\]\(([^)]+)\)', content):
            path = match.group(1)
            if not path or path.startswith('http'):
                continue
            referenced.add(os.path.basename(path).replace('\\', '/'))
        # 匹配 Obsidian wikilinks 到资源文件: [[files/xxx.ext]] 或 ![[files/xxx.ext|别名]]
        for match in re.finditer(r'!?\[\[(files/[^\]|]+)(?:\|[^\]]+)?\]\]', content):
            basename = os.path.basename(match.group(1))
            if basename:
                referenced.add(basename)

    by_type = {}
    for name in referenced:
        ext = get_extension(name).lower()
        by_type.setdefault(ext, set()).add(name)

    return {'referenced': referenced, 'by_type': by_type}


# ═══════════════════════════════════════════════════════════
# ZIP 打包
# ═══════════════════════════════════════════════════════════

def create_obsidian_zip(
        vault_name: str,
        all_pages: list,
        processed_files: dict,
        output_path: pathlib.Path,
        assets_dir: pathlib.Path,
        source_dir_name: str) -> None:
    """
    创建 Obsidian 兼容 ZIP 包。

    ZIP 结构（解压后直接作为 vault 打开）：
        <VaultName>/
          page1.md
          page2.md
          files/
            image.png
            video.mp4
            audio.mp3
            attachment.pdf
          _OBSIDIAN_IMPORT_GUIDE.md  (可选，导入说明)
    """
    # 收集被引用的资源
    assets_info = collect_referenced_assets(processed_files, all_pages)

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        md_count = 0
        asset_count = 0
        written_assets = set()  # 跟踪已写入的资源文件，避免重复

        # 1. 写入所有 .md 页面 → vault_name/
        for page_name in all_pages:
            if page_name not in processed_files:
                continue
            zip_path = f"{vault_name}/{page_name}"
            zf.writestr(zip_path, processed_files[page_name].encode('utf-8'))
            print(f"  [MD]  {zip_path} ({len(processed_files[page_name])} 字符)")
            md_count += 1

        # 2. 写入所有资源文件 → vault_name/files/
        if assets_dir.exists():
            # 先写入被引用的资源
            for item in sorted(assets_dir.iterdir()):
                if not item.is_file():
                    continue
                if item.name.startswith('.') or item.name.startswith('~'):
                    continue
                if item.name.lower() in {'thumbs.db', 'desktop.ini'}:
                    continue

                ext = get_extension(item.name).lower()

                # 只写入被引用的资源或可嵌入媒体
                if item.name not in assets_info['referenced']:
                    continue

                zip_path = f"{vault_name}/{ASSETS_DIR}/{item.name}"
                zf.write(item, zip_path)
                written_assets.add(item.name)
                size = get_file_size_info(item)
                if ext in IMAGE_EXTENSIONS:
                    type_tag = "[IMG]"
                elif ext in VIDEO_EXTENSIONS:
                    type_tag = "[VID]"
                elif ext in AUDIO_EXTENSIONS:
                    type_tag = "[AUD]"
                elif ext == ".pdf":
                    type_tag = "[PDF]"
                else:
                    type_tag = "[ATT]"
                print(f"  {type_tag} {zip_path} ({size})")
                asset_count += 1

            # 再写入未被引用但属于已知资源类型的文件
            unreferenced_count = 0
            for item in sorted(assets_dir.iterdir()):
                if not item.is_file():
                    continue
                if item.name.startswith('.'):
                    continue
                if item.name in written_assets:
                    continue

                ext = get_extension(item.name).lower()
                if ext in ALL_ASSET_EXTENSIONS:
                    zip_path = f"{vault_name}/{ASSETS_DIR}/{item.name}"
                    zf.write(item, zip_path)
                    written_assets.add(item.name)
                    unreferenced_count += 1

            if unreferenced_count:
                print(f"  [ATT] ... 以及 {unreferenced_count} 个未被引用但已包含的附件")

        print(f"  --- 共 {md_count} 个页面 + {len(written_assets)} 个资源文件")

        # 4. 写入导入指南
        guide = generate_import_guide(vault_name, all_pages, processed_files,
                                       assets_info, source_dir_name)
        zf.writestr(f"{vault_name}/_OBSIDIAN_IMPORT_GUIDE.md", guide.encode('utf-8'))
        print(f"  [INF] _OBSIDIAN_IMPORT_GUIDE.md")


def generate_import_guide(vault_name: str, all_pages: list,
                           processed_files: dict, assets_info: dict,
                           source_dir_name: str) -> str:
    """生成 Obsidian 导入指南（Markdown 格式，导入后可直接在 Obsidian 中阅读）。"""

    page_list = '\n'.join(f"- {p}" for p in all_pages if p in processed_files)

    # 资源按类型分组
    asset_lines = []
    for ext in sorted(assets_info.get('by_type', {}).keys()):
        files = sorted(assets_info['by_type'][ext])
        type_name = ""
        if ext in IMAGE_EXTENSIONS:
            type_name = "图片"
        elif ext in VIDEO_EXTENSIONS:
            type_name = "视频"
        elif ext in AUDIO_EXTENSIONS:
            type_name = "音频"
        elif ext == ".pdf":
            type_name = "PDF"
        else:
            type_name = "附件"
        for f in files:
            asset_lines.append(f"- `files/{f}` ({type_name})")

    asset_section = '\n'.join(asset_lines) if asset_lines else "（无资源文件）"

    return f"""---
tags: [import-guide]
---

# Obsidian 导入指南

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 来源: Anytype 导出目录 `{source_dir_name}`

---

## 如何导入

### 方法一：直接打开为 Vault（推荐）

1. 解压本 ZIP 文件
2. 打开 Obsidian
3. 点击左下角 **"Open folder as vault"**（打开文件夹作为库）
4. 选择解压出来的 **`{vault_name}/`** 文件夹
5. Obsidian 自动读取所有 .md 文件、图片和附件，即刻可用

### 方法二：合并到现有 Vault

1. 解压本 ZIP 文件
2. 将 `{vault_name}/` 文件夹内的所有内容复制到你现有的 Obsidian vault 目录中
3. Obsidian 自动刷新，新内容出现在文件列表中

---

## 页面列表（共 {len([p for p in all_pages if p in processed_files])} 个）

{page_list}

---

## 资源文件

{asset_section}

---

## 特性说明

- **Wikilinks**: Markdown 内部链接 `[text](page.md)` 已转为 `[[page|text]]` 格式，支持 Obsidian 图谱、反向链接和文件重命名自动更新
- **YAML frontmatter**: 已保留，可用于 Obsidian Properties 和 Dataview 查询
- **所有 Markdown 样式**: 粗体、斜体、代码、表格等均完整保留，Obsidian 原生支持
- **媒体嵌入**: 图片自动显示，视频/音频使用 `![[file.mp4]]` 语法可内嵌播放
- **PDF**: 使用 `![[file.pdf]]` 语法可内嵌预览

## 推荐插件

以下 Obsidian 社区插件可增强本 vault 的体验：

| 插件 | 用途 |
|------|------|
| **Dataview** | 对页面元数据进行查询和列表 |
| **Calendar** | 日记/日历视图 |
| **Tag Wrangler** | 批量管理标签 |
| **Note Refactor** | 提取选中文本为新笔记 |
| **Excalidraw** | 绘图和白板 |

---

*由 obsidian_import_preprocessor v1.0 生成*
"""


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def process_anytype_dir(work_dir: pathlib.Path) -> str:
    """
    处理单个 Anytype.* 目录：
    1. 扫描所有 .md 文件
    2. 构建文件名映射（H1 标题 → 安全文件名）
    3. 处理每个 .md 文件（保留样式，转换 wikilinks）
    4. 分析页面关系，确定主页面
    5. 打包为 Obsidian 兼容 ZIP
    """
    assets_dir = work_dir / ASSETS_DIR
    dir_name = work_dir.name

    # Step 1: 扫描 .md 文件
    print(f"\n  [1/5] 扫描 Markdown 文件 [{dir_name}]...")
    md_files = sorted(f for f in work_dir.iterdir()
                      if f.is_file() and f.suffix.lower() == '.md')
    print(f"  找到 {len(md_files)} 个 .md 文件:")
    for f in md_files:
        print(f"    - {f.name}")

    if not md_files:
        print("  跳过: 未找到 .md 文件")
        return None

    # Step 2: 构建文件名映射
    print(f"\n  [2/5] 提取 H1 标题，构建文件名映射...")
    file_mapping = build_file_mapping(md_files)
    for old, new in file_mapping.items():
        print(f"  {old} → {new}")

    # Step 3: 处理每个 .md 文件
    print(f"\n  [3/5] 处理 Markdown 内容...")
    processed_files = {}
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')

        # Obsidian 处理管线（比 Notion 版本简单得多）：
        #   1. 修复 Anytype 转义残留（代码块外）
        #   2. 修复链接中的路径分隔符
        #   3. 清理图片 alt text
        #   4. 移除表格 <br> 标签，规范化表格格式
        #   5. 清理尾部空白与多余空行
        #   6. 确保表格前后有正确空行间距
        #   7. 最后转换链接（依赖前面的处理结果）
        content = fix_markdown_escapes(content)
        content = fix_path_separators(content)
        content = process_images(content)
        content = fix_table_br_tags(content)
        content = clean_trailing_whitespace(content)
        content = fix_table_spacing(content)
        # wikilink 转换最后做，因为需要依赖清理后的路径
        content = convert_links_to_wikilinks(content, file_mapping)

        new_name = file_mapping[md_file.name]
        processed_files[new_name] = content
        print(f"  OK {new_name} ({len(content)} 字符)")

    # Step 4: 分析页面关系
    print(f"\n  [4/5] 分析页面链接关系...")
    hub_file, hub_name = find_hub_page(md_files, file_mapping)
    hub_title = extract_h1_title(hub_file.read_text(encoding='utf-8'))
    print(f"  主页面: {hub_name} (H1: {hub_title})")

    # 收集所有页面（主页面优先）
    all_pages = [hub_name]
    for name in sorted(processed_files.keys()):
        if name != hub_name:
            all_pages.append(name)
    print(f"  页面总数: {len(all_pages)}")

    # Step 5: 打包 ZIP
    vault_name = sanitize_filename(hub_title) if hub_title else "Obsidian_Vault"
    zip_filename = f"obsidian_import_{vault_name}.zip"
    output_zip = WORK_DIR / zip_filename
    print(f"\n  [5/5] 打包 Obsidian ZIP → {zip_filename}...")

    fd, tmp_path = tempfile.mkstemp(suffix='.zip', dir=str(WORK_DIR))
    os.close(fd)
    try:
        create_obsidian_zip(
            vault_name, all_pages, processed_files,
            pathlib.Path(tmp_path), assets_dir, dir_name
        )
        if output_zip.exists():
            output_zip.unlink()
        pathlib.Path(tmp_path).rename(output_zip)
    except Exception:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        raise

    zip_size = get_file_size_info(output_zip)
    print(f"  完成: {output_zip.name} ({zip_size})")
    return str(output_zip)


def main():
    print("=" * 60)
    print("  Obsidian 导入预处理工具 v1.0")
    print("=" * 60)

    work_dirs = discover_all_work_dirs()

    if not work_dirs:
        print("\n  错误: 未找到任何 Anytype.* 子目录")
        print("  请将本脚本放在包含 Anytype 导出文件夹的目录中运行")
        print("  例如：")
        print("    your_folder/")
        print("      obsidian_import_preprocessor.py")
        print("      Anytype.20260603.212731.18/")
        print("        page1.md")
        print("        page2.md")
        print("        files/")
        print("          image.png")
        print("          video.mp4")
        return

    print(f"\n  发现 {len(work_dirs)} 个 Anytype 导出目录:")
    for i, d in enumerate(work_dirs, 1):
        md_count = len(list(d.glob('*.md')))
        asset_count = 0
        assets_d = d / ASSETS_DIR
        if assets_d.exists():
            asset_count = sum(1 for f in assets_d.iterdir() if f.is_file())
        print(f"    [{i}] {d.name} ({md_count} 个 .md 文件, {asset_count} 个资源文件)")

    results = []
    for i, work_dir in enumerate(work_dirs, 1):
        print(f"\n{'=' * 60}")
        print(f"  处理 [{i}/{len(work_dirs)}]: {work_dir.name}")
        print(f"{'=' * 60}")

        try:
            output = process_anytype_dir(work_dir)
            if output:
                results.append(output)
        except Exception as e:
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  全部完成! 共生成 {len(results)} 个 ZIP:")
    print(f"{'=' * 60}")
    for r in results:
        size = get_file_size_info(pathlib.Path(r))
        print(f"  - {pathlib.Path(r).name} ({size})")

    print(f"\n  导入 Obsidian 步骤:")
    print(f"  1. 解压 ZIP 文件")
    print(f"  2. 打开 Obsidian → Open folder as vault → 选择解压出的文件夹")
    print(f"  3. 完成！所有页面、图片、附件即刻可用")
    print(f"\n  提示: 无需额外插件即可导入，ZIP 解压 = Vault 文件夹")


if __name__ == '__main__':
    main()
