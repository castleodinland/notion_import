#!/usr/bin/env python3
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

"""
Notion 导入预处理脚本 v1.0
将 Anytype 导出的一组互链 Markdown 文件转换为 Notion 兼容格式，并打包为 ZIP。

特点：
- 不修改源目录中任何文件，全部在内存中处理
- 支持多个 .md 文件互相链接
- Markdown 文件名用 H1 标题替换
- 自动更新文件间的链接引用
- 只打包图片，附件需手动上传
- 附件使用绝对路径标记，方便手动定位
- ZIP 名称使用最上层 md 文件的 H1 标题
- MD 文件直接放在 ZIP 根目录（不带子目录包装），确保 Notion 能正确解析图片相对路径

用法：
    python notion_import_preprocessor.py

ZIP 结构：
    加密授权支持.md
    加解密基本流程.md
    files/
      image.png
      ...
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
    自动发现所有包含 .md 文件的工作目录。
    策略：
      - 脚本所在目录直接有 .md 文件 → 返回 [脚本目录]
      - 否则搜索所有 Anytype.* 子目录 → 返回所有包含 .md 的子目录列表
    """
    # 检查脚本所在目录
    local_mds = list(WORK_DIR.glob('*.md'))
    if local_mds:
        return [WORK_DIR]

    # 搜索所有 Anytype.* 子目录
    dirs = []
    for subdir in sorted(WORK_DIR.iterdir()):
        if subdir.is_dir() and subdir.name.startswith('Anytype'):
            subdir_mds = list(subdir.glob('*.md'))
            if subdir_mds:
                dirs.append(subdir)

    return dirs

# 资源目录（相对于 WORK_DIR）
ASSETS_DIR = "files"

# 输出 ZIP 文件名（动态生成：notion_import_(最上层md标题).zip）
ZIP_OUTPUT_NAME = None  # 在 main() 中根据最上层 md 标题动态设置

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
    """将 Markdown 中反斜杠路径转为正斜杠"""
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
    例如：D:\\customer\\huwen\\... → D:/customer/huwen/...
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

        # 匹配 Windows 盘符路径（如 D:\xxx），不在链接中
        pattern = r'[A-Za-z]:\\[^\s]*'

        def replace_path(m):
            path = m.group(0)
            # 先移除已有的转义序列（如 \_ → _），再统一替换为正斜杠
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
        # 只在表格行中处理（包含 | 且不在代码块中）
        if '|' in line and not in_code_block and not stripped.startswith('```'):
            # 替换 <br> 及其变体（<br>, <br/>, <br />）为空格
            line = re.sub(r'\s*<br\s*/?>\s*', ' ', line)
            # 规范化 | 周围的多余空格（排除转义的 \|）
            # 但跳过分隔行（仅含 |, :, -, 空格），避免破坏 |:--- 对齐语法
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
    图片链接保持原样，其余所有本地文件链接一律视为附件。
    """
    lines = content.split('\n')
    processed_lines = []

    # 将 assets_dir 转为绝对路径（Windows 反斜杠格式，方便直接复制到资源管理器）
    assets_abs = str(assets_dir.resolve())

    for line in lines:
        stripped = line.strip()
        link_pattern = r'^\[([^\]]+)\]\(([^)]+)\)$'
        match = re.match(link_pattern, stripped)

        if match:
            link_text = match.group(1)
            link_path = match.group(2)

            # 跳过外部链接（http/https）
            if link_path.startswith('http://') or link_path.startswith('https://'):
                processed_lines.append(line)
                continue

            ext = get_extension(link_path).lower()

            if ext in IMAGE_EXTENSIONS:
                # 图片保持原样（会打包到 ZIP 中）
                processed_lines.append(line)
            else:
                # 所有非图片的本地文件链接一律视为附件
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
    将 H1 标题转为安全的文件名。
    保留中文、英文、数字、下划线、连字符、括号。
    """
    # 替换不安全字符为下划线
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)
    # 去除首尾空白和点
    safe = safe.strip('. ')
    # 压缩连续下划线
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
    # 匹配 ]( 在行首（前面只有空白），将上一行末尾的换行删除
    content = re.sub(r'\n(\s*)\]', ']', content)
    return content


def update_internal_links(content: str, file_mapping: dict) -> str:
    """
    更新 Markdown 中指向其他 .md 文件的链接。
    例如：[名称](dai-ma-bao-bei-fen.md) → [名称](代码包备份.md)
    """
    # 先规范化跨行链接
    content = normalize_multiline_links(content)

    def replace_link(match):
        link_text = match.group(1)
        link_path = match.group(2)

        # 只处理本地 .md 文件链接
        if link_path.startswith('http://') or link_path.startswith('https://'):
            return match.group(0)

        basename = os.path.basename(link_path)
        if basename in file_mapping:
            new_path = link_path.replace(basename, file_mapping[basename])
            new_path = new_path.replace('\\', '/')
            return f"[{link_text}]({new_path})"

        return match.group(0)

    # 匹配 [text](path) 格式
    pattern = r'\[([^\]]*?)\]\(([^)]+)\)'
    return re.sub(pattern, replace_link, content)


# ═══════════════════════════════════════════════════════════
# 资源收集
# ═══════════════════════════════════════════════════════════

def collect_referenced_assets(md_files: list, assets_dir: pathlib.Path) -> set:
    """
    扫描所有 md 文件中引用的资源文件，返回需要打包的文件集合。
    """
    referenced = set()
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')
        # 查找所有 ![...](...) 和 [...](...) 引用
        pattern = r'[!]\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)'
        for match in re.finditer(pattern, content):
            path = match.group(1) or match.group(2)
            if not path:
                continue
            # 跳过外部链接
            if path.startswith('http://') or path.startswith('https://'):
                continue
            basename = os.path.basename(path).replace('\\', '/')
            if basename:
                referenced.add(basename)

    return referenced


# ═══════════════════════════════════════════════════════════
# ZIP 打包
# ═══════════════════════════════════════════════════════════

def create_notion_zip(processed_files: dict, output_path: pathlib.Path,
                     assets_dir: pathlib.Path) -> None:
    """
    创建 Notion 兼容 ZIP 包。
    processed_files: { "新文件名.md": "文件内容", ... }
    assets_dir: 资源文件的绝对目录路径
    """
    # 收集所有被引用的资源文件名
    all_referenced = set()
    for content in processed_files.values():
        pattern = r'[!]\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)'
        for match in re.finditer(pattern, content):
            path = match.group(1) or match.group(2)
            if not path or path.startswith('http'):
                continue
            all_referenced.add(os.path.basename(path).replace('\\', '/'))

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. 写入处理后的 .md 文件（直接放在 ZIP 根目录，不带子目录包装）
        for new_name, content in sorted(processed_files.items()):
            zf.writestr(new_name, content.encode('utf-8'))
            print(f"  [MD]  {new_name}")

        # 2. 写入被引用的资源文件
        if assets_dir.exists():
            asset_count = 0
            for item in sorted(assets_dir.iterdir()):
                if not item.is_file():
                    continue
                if item.name.startswith('.') or item.name.startswith('~'):
                    continue
                if item.name.lower() == 'thumbs.db':
                    continue
                if item.name in all_referenced:
                    ext = get_extension(item.name).lower()
                    # 只打包图片，附件需要手动上传到 Notion
                    if ext not in IMAGE_EXTENSIONS:
                        continue
                    zip_path = f"{ASSETS_DIR}/{item.name}"
                    zf.write(item, zip_path)
                    size = get_file_size_info(item)
                    print(f"  [IMG] {zip_path} ({size})")
                    asset_count += 1

            print(f"  --- 共 {asset_count} 个资源文件")

        # 3. 写入 README
        readme = generate_readme(processed_files, all_referenced, assets_dir)
        zf.writestr("README_IMPORT_GUIDE.txt", readme.encode('utf-8'))
        print(f"  [INF] README_IMPORT_GUIDE.txt")


def generate_readme(processed_files: dict, referenced_assets: set,
                  assets_dir: pathlib.Path) -> str:
    """生成导入说明"""

    file_list = '\n'.join(f"  - {name}" for name in sorted(processed_files.keys()))

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
        image_section = "\n【图片资源（自动内嵌）】\n" + '\n'.join(f"  - {name}" for name in image_assets) + '\n'

    attachment_section = ""
    if other_assets:
        lines = []
        for name in other_assets:
            lines.append(f"  - {name}")
        attachment_section = "\n【附件（需手动上传到 Notion）】\n" + '\n'.join(lines) + '\n'

    return f"""============================================================
  Notion 导入说明
  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
============================================================

【导入步骤】
1. 打开 Notion (https://www.notion.so)
2. 在左侧边栏底部找到 "Import"
3. 选择 "Text & Markdown"，上传本 ZIP 文件 ({ZIP_OUTPUT_NAME})
4. Notion 会自动识别 .md 文件并创建对应的页面

【页面清单】（共 {len(processed_files)} 个）
{file_list}
{image_section}{attachment_section}
【关于页面间链接】
- Markdown 文件间的互链（如 [名称](xxx.md)）已更新为新的文件名
- Notion 导入后，页面间链接会显示为普通文本链接（非 Notion 内部链接）
- 如需建立 Notion 内部链接，导入后手动替换即可

【关于附件】
- Notion 的 Markdown ZIP 导入不支持直接嵌入二进制文件
- 附件未包含在 ZIP 中，需从本地 files/ 目录手动上传到 Notion 页面
- 上传方法：在 Notion 页面中输入 /file 创建文件块，选择对应文件上传
- 文档中已用 📎 emoji 标记每个附件的完整路径和文件名，方便快速定位
"""


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def find_top_level_md(md_files: list, file_mapping: dict) -> str:
    """
    找到最上层的 md 文件。
    策略：包含最多内部 .md 链接的文件视为主页面（hub 页面）。
    """
    max_links = -1
    top_name = file_mapping[md_files[0].name]  # fallback

    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')
        links = re.findall(r'\[([^\]]+)\]\(([^)]+\.md)\)', content)
        if len(links) > max_links:
            max_links = len(links)
            top_name = file_mapping[md_file.name]

    return top_name


def process_work_dir(work_dir: pathlib.Path) -> str:
    """
    处理单个 Anytype 目录，生成 ZIP。
    返回输出的 ZIP 路径字符串，失败返回 None。
    """
    assets_dir = work_dir / ASSETS_DIR

    # Step 1: 发现所有 .md 文件
    print(f"\n  [1/4] 扫描 Markdown 文件...")
    md_files = sorted(f for f in work_dir.iterdir()
                      if f.is_file() and f.suffix.lower() == '.md')
    print(f"  找到 {len(md_files)} 个 .md 文件:")
    for f in md_files:
        print(f"    - {f.name}")

    if not md_files:
        print("  跳过: 未找到 .md 文件")
        return None

    # Step 2: 构建 H1 标题 → 文件名映射
    print(f"\n  [2/4] 提取 H1 标题，构建文件名映射...")
    file_mapping = build_file_mapping(md_files)

    # 确定最上层 md 标题，用于 ZIP 文件名
    top_title = find_top_level_md(md_files, file_mapping)
    zip_name = f"notion_import_{top_title}.zip"
    print(f"  最上层文档: {top_title}")
    print(f"  ZIP 名称: {zip_name}")

    # Step 3: 处理每个 .md 文件（全部在内存中，不写源目录）
    print(f"\n  [3/4] 处理 Markdown 内容（不修改源文件）...")
    processed_files = {}
    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8')

        # 去除 YAML frontmatter
        content = remove_yaml_frontmatter(content)

        # 更新内部链接（文件名 → H1 标题）
        content = update_internal_links(content, file_mapping)

        # 修复路径分隔符（链接中的反斜杠）
        content = fix_path_separators(content)

        # 修复纯文本中的 Windows 路径反斜杠（避免 Markdown 转义问题）
        content = fix_windows_paths(content)

        # 处理图片语法
        content = process_images(content)

        # 处理二进制附件（使用绝对路径）
        content = process_attachments(content, assets_dir)

        # 移除表格中的 <br> 标签（Notion 无法正确解析）
        content = fix_table_br_tags(content)

        # 清理空白
        content = clean_trailing_whitespace(content)

        new_name = file_mapping[md_file.name]
        processed_files[new_name] = content
        print(f"  OK {new_name} ({len(content)} 字符)")

    # Step 4: 打包 ZIP（写到脚本所在目录，用临时文件确保原子性）
    output_zip = WORK_DIR / zip_name
    print(f"\n  [4/4] 打包 ZIP...")
    fd, tmp_path = tempfile.mkstemp(suffix='.zip', dir=str(WORK_DIR))
    os.close(fd)
    try:
        create_notion_zip(processed_files, pathlib.Path(tmp_path), assets_dir)
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
    print("  Notion 导入预处理工具 v1.0")
    print("=" * 60)

    # 发现所有需要处理的目录
    work_dirs = discover_all_work_dirs()

    if not work_dirs:
        print("\n  错误: 未找到任何包含 .md 文件的目录")
        print("  请将本脚本放在包含 Anytype 导出文件的目录中运行")
        return

    print(f"\n  发现 {len(work_dirs)} 个待处理目录:")
    for i, d in enumerate(work_dirs, 1):
        print(f"    [{i}] {d.name}")

    # 逐个处理
    results = []
    for i, work_dir in enumerate(work_dirs, 1):
        print(f"\n{'=' * 60}")
        print(f"  处理 [{i}/{len(work_dirs)}]: {work_dir.name}")
        print(f"{'=' * 60}")

        try:
            output = process_work_dir(work_dir)
            if output:
                results.append(output)
        except Exception as e:
            print(f"  错误: {e}")

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  全部完成! 共生成 {len(results)} 个 ZIP:")
    print(f"{'=' * 60}")
    for r in results:
        size = get_file_size_info(pathlib.Path(r))
        print(f"  - {pathlib.Path(r).name} ({size})")
    print(f"\n  下一步: 将 ZIP 文件上传到 Notion 导入")


if __name__ == '__main__':
    main()
