#!/usr/bin/env python3
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

"""
Notion 导入预处理脚本 v3.0
将 Anytype 导出的一组互链 Markdown 文件转换为 Notion 兼容格式，并打包为 ZIP。

v3.0 改动（页面层级模式）：
- 利用 Notion ZIP 导入的"文件夹→子页面"特性，构建页面层级树
- 主页面（hub page）作为根文件夹，以 index.md 存放其内容
- 被主页面链接的子页面作为同级 .md 文件放在根文件夹内
- Notion 导入后自然形成「主页面 → 子页面」的父子层级
- 各子页面中的互链保持为 Markdown 链接（导入后为文本链接）
- ZIP 命名：notion_import_<主md标题>.zip，输出到脚本平级目录
- 始终遍历 Anytype.* 平行子目录，每个独立处理

设计原理：
    Notion 导入 ZIP 时，文件夹 → 页面，.md 文件 → 页面。
    导出工具（如 notion-exporter）的标准约定：
      - 无子页面的页面 → PageName.md
      - 有子页面的页面 → PageName/index.md + PageName/Child.md
    我们遵循此约定，使得导入后的页面树与 Anytype 的链接结构一致。

ZIP 结构（v3.0）：
    notion_import_加密授权支持.zip
      加密授权支持/
        index.md                      ← 主页面内容
        名称与项目对照表.md             ← 子页面（Notion 导入后为主页面的子页面）
        代码包备份.md                   ← 子页面
        加解密基本流程(内部文档).md      ← 子页面
        加解密基本流程(对外文档).md      ← 子页面
        files/                        ← 图片资源（与 .md 同级，路径无需变更）
          image.png
          ...
      README_IMPORT_GUIDE.txt

已知限制：
    - Notion 的 ZIP 导入不会将 Markdown 链接 [text](page.md) 转为 Notion 内部页面引用
    - 导入后仍需手动用 Notion 的 /page 命令替换链接为真正的页面引用
    - 这是 Notion 本身的限制，所有第三方工具（notionreposync, md-to-notion 等）
      均通过 Notion API 来绕过此限制

用法：
    python notion_import_preprocessor_v3.0.py
"""

import os
import re
import sys
import zipfile
import pathlib
import tempfile
from datetime import datetime

# ═══════════════════════════════════════════════════════════
# 配置区
# ═══════════════════════════════════════════════════════════

WORK_DIR = pathlib.Path(__file__).parent


def discover_all_work_dirs() -> list:
    """
    自动发现所有 Anytype.* 子目录。
    返回所有包含 .md 文件的 Anytype.* 子目录列表。
    """
    dirs = []
    for subdir in sorted(WORK_DIR.iterdir()):
        if subdir.is_dir() and subdir.name.startswith('Anytype'):
            subdir_mds = list(subdir.glob('*.md'))
            if subdir_mds:
                dirs.append(subdir)

    return dirs


# 资源目录（相对于每个 Anytype 工作目录）
ASSETS_DIR = "files"

# 图片扩展名
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}

# 视频扩展名
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

# 音频扩展名
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}

# Notion Markdown ZIP 导入不支持的附件格式
UNSUPPORTED_EXTENSIONS = {
    ".bin", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".exe", ".msi", ".dmg", ".apk",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".hex", ".elf", ".map", ".axf", ".out",
    ".py", ".drawio", ".txt",
}

# Notion Markdown ZIP 导入支持的文本格式（直接内嵌为页面内容）
SUPPORTED_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm"}

# 需要清理的目录/文件（不打包进 ZIP）
EXCLUDE_DIRS = {".workbuddy", "schemas", "__pycache__"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", "desktop.ini",
                 "notion_import_preprocessor.py",
                 "notion_import_preprocessor_v1.0.py",
                 "notion_import_preprocessor_v2.0.py",
                 "notion_import_preprocessor_v3.0.py",
                 "markdown_to_html_v12.19.py",
                 "notion_ready"}

# ═══════════════════════════════════════════════════════════
# Markdown 处理函数
# ═══════════════════════════════════════════════════════════

def remove_yaml_frontmatter(content: str) -> str:
    """去除 YAML frontmatter"""
    pattern = r'^---\s*\n.*?\n---\s*\n'
    return re.sub(pattern, '', content, count=1, flags=re.DOTALL)


def extract_h1_title(content: str) -> str:
    """提取 Markdown 的第一个 H1 标题"""
    content_clean = remove_yaml_frontmatter(content)
    match = re.search(r'^#\s+(.+?)\s*$', content_clean, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def fix_path_separators(content: str) -> str:
    """将 Markdown 中反斜杠路径转为正斜杠（仅链接内）"""
    pattern = r'(!?\[[^\]]*\]\()([^)]+)(\))'

    def replace_path(match):
        prefix = match.group(1)
        path = match.group(2)
        suffix = match.group(3)
        fixed_path = path.replace('\\', '/')
        return f"{prefix}{fixed_path}{suffix}"

    return re.sub(pattern, replace_path, content)


def fix_windows_paths(content: str) -> str:
    """
    将纯文本中的 Windows 路径反斜杠替换为正斜杠。
    Markdown 中反斜杠是转义字符，\\c \\a \\e 等会被解析为转义序列，显示错乱。
    """
    lines = content.split('\n')
    result = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
        if in_code_block or stripped.startswith('```'):
            result.append(line)
            continue

        pattern = r'[A-Za-z]:\\[^\s]*'

        def replace_path(m):
            path = m.group(0)
            path = path.replace('\\_', '_')
            path = path.replace('\\', '/')
            return path

        line = re.sub(pattern, replace_path, line)
        result.append(line)

    return '\n'.join(result)


def fix_table_br_tags(content: str) -> str:
    """
    移除表格单元格中的 <br> HTML 标签。
    Anytype 导出的表格每行末尾都有 ' <br>'，Notion 会将其当作单元格内换行，
    导致表格被拆散为多个零散块。
    """
    lines = content.split('\n')
    result = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
        if '|' in line and not in_code_block and not stripped.startswith('```'):
            line = re.sub(r'\s*<br\s*/?>\s*', ' ', line)
            sep_stripped = line.strip()
            if not re.match(r'^[\s|:\-]+$', sep_stripped):
                line = re.sub(r'(?<!\\)\s*\|\s*(?!\\)', ' | ', line)
            line = line.strip()
        result.append(line)

    return '\n'.join(result)


def clean_trailing_whitespace(content: str) -> str:
    """清理每行末尾空格、连续空行、尾部空行"""
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


def process_images(content: str) -> str:
    """确保图片语法正确，清理 alt text 多余空格"""
    def fix_image(match):
        alt = match.group(1).strip()
        path = match.group(2)
        return f'![{alt}]({path})'

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_image, content)


def process_attachments(content: str, assets_dir: pathlib.Path) -> str:
    """
    处理二进制附件链接，转为 Notion 引用块格式（使用绝对路径）。
    图片链接保持原样，.md 内部页面链接保持原样，其余本地文件链接视为附件。
    """
    lines = content.split('\n')
    processed_lines = []
    assets_abs = str(assets_dir.resolve())

    for line in lines:
        stripped = line.strip()
        link_pattern = r'^\[([^\]]+)\]\((.+)\)$'
        match = re.match(link_pattern, stripped)

        if match:
            link_text = match.group(1)
            link_path = match.group(2)

            if link_path.startswith('http://') or link_path.startswith('https://'):
                processed_lines.append(line)
                continue

            ext = get_extension(link_path).lower()

            if ext in IMAGE_EXTENSIONS:
                processed_lines.append(line)
            elif ext == '.md':
                # .md 内部页面链接保持原样
                processed_lines.append(line)
            else:
                file_name = os.path.basename(link_path)
                abs_path = assets_abs + '\\' + file_name
                processed_lines.append('')
                processed_lines.append(f'> 📎 **附件：`{abs_path}`**')
                processed_lines.append('')
        else:
            processed_lines.append(line)

    return '\n'.join(processed_lines)


def get_extension(filepath: str) -> str:
    return os.path.splitext(filepath)[1]


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
    """
    将 H1 标题转为安全的文件名/文件夹名。
    保留中文、英文、数字、下划线、连字符、括号。
    """
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)
    safe = safe.strip('. ')
    safe = re.sub(r'_+', '_', safe)
    return safe


# ═══════════════════════════════════════════════════════════
# 多文件链接处理
# ═══════════════════════════════════════════════════════════

def build_file_mapping(md_files: list) -> dict:
    """
    构建原始文件名 → 新文件名(H1标题) 的映射。
    返回: { "original.md": "H1标题.md", ... }
    """
    mapping = {}
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')
        title = extract_h1_title(content)
        if title:
            new_name = sanitize_filename(title) + '.md'
            mapping[md_file.name] = new_name
            print(f"  {md_file.name} → {new_name}")
        else:
            mapping[md_file.name] = md_file.name
            print(f"  {md_file.name} → {md_file.name} (无 H1 标题)")
    return mapping


def normalize_multiline_links(content: str) -> str:
    """
    规范化跨行链接为单行。
    Anytype 导出的 Markdown 中，链接文本和路径常分两行：
      [加解密基本流程(内部文档)
      ](xxx.md)
    转为：
      [加解密基本流程(内部文档)](xxx.md)
    """
    content = re.sub(r'\n(\s*)\]', ']', content)
    return content


def update_internal_links(content: str, file_mapping: dict) -> str:
    """
    更新 Markdown 中指向其他 .md 文件的链接。
    例如：[名称](dai-ma-bao-bei-fen.md) → [名称](代码包备份.md)
    """
    content = normalize_multiline_links(content)

    def replace_link(match):
        link_text = match.group(1)
        link_path = match.group(2)

        if link_path.startswith('http://') or link_path.startswith('https://'):
            return match.group(0)

        basename = os.path.basename(link_path)
        if basename in file_mapping:
            new_path = link_path.replace(basename, file_mapping[basename])
            new_path = new_path.replace('\\', '/')
            return f"[{link_text}]({new_path})"

        return match.group(0)

    pattern = r'\[([^\]]*?)\]\(([^)]+)\)'
    return re.sub(pattern, replace_link, content)


# ═══════════════════════════════════════════════════════════
# v3.0 新增：页面树与 ZIP 层级结构
# ═══════════════════════════════════════════════════════════

def find_hub_page(md_files: list, file_mapping: dict) -> tuple:
    """
    找到主页面（hub page）。
    策略：包含最多内部 .md 链接的文件视为主页面。
    返回: (原始文件 pathlib.Path, 新文件名 str)
    """
    max_links = -1
    hub_file = md_files[0]
    hub_name = file_mapping[md_files[0].name]

    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')
        # 贪婪匹配 (.+\.md) 支持路径中含括号（如 加解密基本流程(对外文档).md）
        links = re.findall(r'\[([^\]]+)\]\((.+\.md)\)', content)
        if len(links) > max_links:
            max_links = len(links)
            hub_file = md_file
            hub_name = file_mapping[md_file.name]

    return hub_file, hub_name


def discover_linked_pages(content: str, file_mapping: dict) -> set:
    """
    从页面内容中提取所有被引用的 .md 文件名（新名称）。
    返回: {"代码包备份.md", "名称与项目对照表.md", ...}
    """
    linked = set()
    # 贪婪匹配 (.+\.md) 支持路径中含括号
    pattern = r'\[([^\]]*?)\]\((.+\.md)\)'
    for match in re.finditer(pattern, content):
        link_path = match.group(2)
        if link_path.startswith('http://') or link_path.startswith('https://'):
            continue
        basename = os.path.basename(link_path)
        if basename in file_mapping:
            linked.add(file_mapping[basename])
        elif basename in {v for v in file_mapping.values()}:
            linked.add(basename)
    return linked


def build_page_tree(md_files: list, file_mapping: dict,
                    processed_files: dict) -> dict:
    """
    分析页面之间的链接关系，构建页面树。

    返回:
        {
            'hub_name': '加密授权支持.md',     # 主页面新文件名
            'hub_title': '加密授权支持',        # 主页面 H1（用于 ZIP 命名和文件夹名）
            'hub_folder': '加密授权支持',       # 主文件夹名
            'children': ['名称与项目对照表.md', ...],  # 直接子页面
            'orphans': ['某孤立页面.md', ...],  # 未被主页面链接的页面
        }
    """
    hub_file, hub_name = find_hub_page(md_files, file_mapping)
    hub_content = processed_files[hub_name]
    hub_title = extract_h1_title(hub_file.read_text(encoding='utf-8'))

    # 从主页面内容中发现被链接的子页面
    linked_children = discover_linked_pages(hub_content, file_mapping)

    # 递归发现更深层级的链接（子页面链接的其他页面也作为该子页面的同级）
    all_linked = set(linked_children)
    for child in list(linked_children):
        if child in processed_files:
            sub_linked = discover_linked_pages(processed_files[child], file_mapping)
            all_linked.update(sub_linked)

    # 排除主页面自身
    all_linked.discard(hub_name)

    # 孤立页面 = 全部页面 - 主页面 - 所有被链接的页面
    orphans = [name for name in processed_files
               if name != hub_name and name not in all_linked]

    return {
        'hub_name': hub_name,
        'hub_title': hub_title,
        'hub_folder': sanitize_filename(hub_title or 'notion_import'),
        'children': sorted(all_linked),
        'orphans': sorted(orphans),
    }


# ═══════════════════════════════════════════════════════════
# ZIP 打包（v3.0 层级模式）
# ═══════════════════════════════════════════════════════════

def create_notion_zip_hierarchical(
        page_tree: dict,
        processed_files: dict,
        output_path: pathlib.Path,
        assets_dir: pathlib.Path,
        source_dir_name: str) -> None:
    """
    创建 Notion 兼容 ZIP 包（v3.0 页面层级模式）。

    ZIP 结构：
        <hub_folder>/
          index.md            ← 主页面内容
          <child1>.md         ← 子页面
          <child2>.md         ← 子页面
          files/              ← 所有被引用的图片
            image.png
            ...
        README_IMPORT_GUIDE.txt

    关键设计：
    - 主页面放在 hub_folder/index.md，作为文件夹的"首页"
    - 子页面 .md 与 index.md 同级，Notion 导入后成为子页面
    - files/ 与所有 .md 同级，图片路径 files/xxx.png 无需修改
    """
    hub_folder = page_tree['hub_folder']
    hub_name = page_tree['hub_name']
    children = page_tree['children']
    orphans = page_tree['orphans']

    # 收集所有被引用的资源文件名（从主页面和所有子页面）
    all_referenced = set()
    all_content = [processed_files[hub_name]]
    for child in children:
        if child in processed_files:
            all_content.append(processed_files[child])
    for orphan in orphans:
        if orphan in processed_files:
            all_content.append(processed_files[orphan])

    for content in all_content:
        pattern = r'[!]\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)'
        for match in re.finditer(pattern, content):
            path = match.group(1) or match.group(2)
            if not path or path.startswith('http'):
                continue
            all_referenced.add(os.path.basename(path).replace('\\', '/'))

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        md_count = 0
        img_count = 0

        # 1. 写入主页面 → hub_folder/index.md
        zip_path = f"{hub_folder}/index.md"
        zf.writestr(zip_path, processed_files[hub_name].encode('utf-8'))
        print(f"  [MD]  {zip_path} ← 主页面 ({len(processed_files[hub_name])} 字符)")
        md_count += 1

        # 2. 写入子页面 → hub_folder/<child>.md
        for child in children:
            if child in processed_files:
                zip_path = f"{hub_folder}/{child}"
                zf.writestr(zip_path, processed_files[child].encode('utf-8'))
                print(f"  [MD]  {zip_path} ← 子页面 ({len(processed_files[child])} 字符)")
                md_count += 1

        # 3. 写入孤立页面 → ZIP 根目录（不在 hub_folder 内）
        for orphan in orphans:
            if orphan in processed_files:
                zf.writestr(orphan, processed_files[orphan].encode('utf-8'))
                print(f"  [MD]  {orphan} ← 孤立页面 ({len(processed_files[orphan])} 字符)")
                md_count += 1

        # 4. 写入被引用的图片资源 → hub_folder/files/
        if assets_dir.exists():
            for item in sorted(assets_dir.iterdir()):
                if not item.is_file():
                    continue
                if item.name.startswith('.') or item.name.startswith('~'):
                    continue
                if item.name.lower() == 'thumbs.db':
                    continue
                if item.name in all_referenced:
                    ext = get_extension(item.name).lower()
                    if ext not in IMAGE_EXTENSIONS:
                        continue
                    zip_path = f"{hub_folder}/{ASSETS_DIR}/{item.name}"
                    zf.write(item, zip_path)
                    size = get_file_size_info(item)
                    print(f"  [IMG] {zip_path} ({size})")
                    img_count += 1

        print(f"  --- 共 {md_count} 个页面 + {img_count} 个图片")

        # 5. 写入 README（ZIP 根目录）
        readme = generate_readme_v3(page_tree, children, orphans,
                                    all_referenced, assets_dir, source_dir_name)
        zf.writestr("README_IMPORT_GUIDE.txt", readme.encode('utf-8'))
        print(f"  [INF] README_IMPORT_GUIDE.txt")


def generate_readme_v3(page_tree: dict, children: list, orphans: list,
                       referenced_assets: set, assets_dir: pathlib.Path,
                       source_dir_name: str) -> str:
    """生成 v3.0 导入说明"""

    hub_folder = page_tree['hub_folder']
    hub_title = page_tree['hub_title'] or hub_folder

    # 子页面清单
    children_list = '\n'.join(f"  - {c}" for c in children) if children else "  （无）"
    orphans_list = '\n'.join(f"  - {o}" for o in orphans) if orphans else "  （无）"

    # 资源分类
    image_assets = []
    other_assets = []
    for name in sorted(referenced_assets):
        ext = get_extension(name).lower()
        if ext in IMAGE_EXTENSIONS:
            image_assets.append(name)
        else:
            other_assets.append(name)

    image_section = ""
    if image_assets:
        image_section = ("\n【图片资源（自动内嵌）】\n" +
                         '\n'.join(f"  - {name}" for name in image_assets) + '\n')

    attachment_section = ""
    if other_assets:
        attachment_section = ("\n【附件（需手动上传到 Notion）】\n" +
                              '\n'.join(f"  - {name}" for name in other_assets) + '\n')

    return f"""============================================================
  Notion 导入说明 (v3.0 — 页面层级模式)
  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  来源目录: {source_dir_name}
============================================================

【导入步骤】
1. 打开 Notion (https://www.notion.so)
2. 在左侧边栏底部找到 "Import"
3. 选择 "Text & Markdown"，上传本 ZIP 文件
4. Notion 会自动识别文件夹层级并创建页面树

【页面层级结构】
  {hub_title}/                  ← 导入后为父页面
  ├── index.md                 ← 父页面的内容
  ├── <子页面>.md              ← 子页面（与 index.md 同级）
  └── files/                   ← 图片资源

【主页面】
  - {hub_title} (文件夹: {hub_folder}/index.md)

【子页面】（{len(children)} 个，导入后为 {hub_title} 的子页面）
{children_list}

【孤立页面】（{len(orphans)} 个，未被主页面链接，放在 ZIP 根目录）
{orphans_list}
{image_section}{attachment_section}
【关于页面间链接】
- Markdown 文件间的互链（如 [名称](xxx.md)）已更新为新的文件名
- ⚠ Notion 的 ZIP 导入不会将 Markdown 链接转为内部页面引用
- 导入后，页面间的链接会显示为普通文本链接
- 如需建立 Notion 内部链接，请使用 /page 命令手动替换
- 页面层级已通过文件夹结构保留，可在侧边栏中看到父子关系

【关于附件】
- Notion 的 Markdown ZIP 导入不支持直接嵌入二进制文件
- 附件未包含在 ZIP 中，需从本地 files/ 目录手动上传到 Notion 页面
- 文档中已用 📎 emoji 标记每个附件的完整路径和文件名，方便快速定位
"""


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def process_anytype_dir(work_dir: pathlib.Path) -> str:
    """
    处理单个 Anytype.* 目录：
    1. 发现并处理所有 .md 文件
    2. 分析链接关系，构建页面树
    3. 按 Notion 层级约定打包 ZIP
    返回输出的 ZIP 路径字符串，失败返回 None。
    """
    assets_dir = work_dir / ASSETS_DIR
    dir_name = work_dir.name

    # Step 1: 发现所有 .md 文件
    print(f"\n  [1/5] 扫描 Markdown 文件 [{dir_name}]...")
    md_files = sorted(f for f in work_dir.iterdir()
                      if f.is_file() and f.suffix.lower() == '.md')
    print(f"  找到 {len(md_files)} 个 .md 文件:")
    for f in md_files:
        print(f"    - {f.name}")

    if not md_files:
        print("  跳过: 未找到 .md 文件")
        return None

    # Step 2: 构建 H1 标题 → 文件名映射
    print(f"\n  [2/5] 提取 H1 标题，构建文件名映射...")
    file_mapping = build_file_mapping(md_files)

    # Step 3: 处理每个 .md 文件（全部在内存中）
    print(f"\n  [3/5] 处理 Markdown 内容（不修改源文件）...")
    processed_files = {}
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')

        content = remove_yaml_frontmatter(content)
        content = update_internal_links(content, file_mapping)
        content = fix_path_separators(content)
        content = fix_windows_paths(content)
        content = process_images(content)
        content = process_attachments(content, assets_dir)
        content = fix_table_br_tags(content)
        content = clean_trailing_whitespace(content)

        new_name = file_mapping[md_file.name]
        processed_files[new_name] = content
        print(f"  OK {new_name} ({len(content)} 字符)")

    # Step 4: 分析链接关系，构建页面树（v3.0 核心）
    print(f"\n  [4/5] 分析页面链接关系，构建层级结构...")
    page_tree = build_page_tree(md_files, file_mapping, processed_files)

    print(f"  主页面: {page_tree['hub_name']} (H1: {page_tree['hub_title']})")
    print(f"  页面树根文件夹: {page_tree['hub_folder']}/")
    print(f"  子页面 ({len(page_tree['children'])} 个):")
    for child in page_tree['children']:
        print(f"    - {child}")
    if page_tree['orphans']:
        print(f"  孤立页面 ({len(page_tree['orphans'])} 个，放在 ZIP 根目录):")
        for orphan in page_tree['orphans']:
            print(f"    - {orphan}")

    # Step 5: 打包 ZIP 到脚本平级目录
    zip_title = sanitize_filename(page_tree['hub_title'] or page_tree['hub_name'])
    zip_filename = f"notion_import_{zip_title}.zip"
    output_zip = WORK_DIR / zip_filename
    print(f"\n  [5/5] 打包层级 ZIP → {zip_filename}...")

    fd, tmp_path = tempfile.mkstemp(suffix='.zip', dir=str(WORK_DIR))
    os.close(fd)
    try:
        create_notion_zip_hierarchical(
            page_tree, processed_files,
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
    print("  Notion 导入预处理工具 v3.0 (页面层级模式)")
    print("=" * 60)

    work_dirs = discover_all_work_dirs()

    if not work_dirs:
        print("\n  错误: 未找到任何 Anytype.* 子目录")
        print("  请将本脚本放在包含 Anytype 导出文件夹的目录中运行")
        print("  例如：")
        print("    your_folder/")
        print("      notion_import_preprocessor_v3.0.py")
        print("      Anytype.20260603.212731.18/")
        print("        page1.md")
        print("        page2.md")
        print("        files/")
        return

    print(f"\n  发现 {len(work_dirs)} 个 Anytype 导出目录:")
    for i, d in enumerate(work_dirs, 1):
        md_count = len(list(d.glob('*.md')))
        print(f"    [{i}] {d.name} ({md_count} 个 .md 文件)")

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
    print(f"\n  下一步: 将 ZIP 文件上传到 Notion 导入")
    print(f"  提示: 导入后页面层级由文件夹结构保留，链接需手动替换为 /page")


if __name__ == '__main__':
    main()
