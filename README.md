# Notion Import Preprocessor

A Python tool that converts [Anytype](https://anytype.io/)-exported Markdown files into Notion-compatible ZIP imports.

## Features

- **Non-destructive processing** - All operations are in-memory; source files are never modified
- **Multi-file linking** - Preserves and updates inter-file Markdown links
- **Smart file renaming** - Renames `.md` files using their H1 title
- **Table fix** - Removes `<br>` tags from table cells that break Notion table rendering
- **Windows path fix** - Converts backslash paths in plain text to forward slashes
- **Multiline link normalization** - Joins Anytype's split-across-lines link format
- **Image auto-packaging** - Bundles referenced images into the ZIP
- **Attachment path labels** - Non-image files are converted to `📎` blocks with full absolute paths for easy manual upload
- **Multi-directory support** - Auto-discovers and processes multiple `Anytype.*` subdirectories independently
- **Atomic ZIP writes** - Uses temp file + rename to prevent corrupted output

## File Processing Rules

| Type | Handling | Packed into ZIP? |
|------|----------|-----------------|
| Images (png/jpg/gif/svg...) | Embedded as `![alt](path)` | Yes |
| Video (mp4/mov/webm...) | Attachment with full path | No |
| Audio (wav/mp3/ogg...) | Attachment with full path | No |
| Code/Doc (c/py/m/txt...) | Attachment with full path | No |
| Binary/Archive (bin/zip/...) | Attachment with full path | No |

## Usage

1. Export your pages from Anytype as Markdown
2. Place the exported files in a directory structure like:
   ```
   your_folder/
     notion_import_preprocessor_v1.0.py
     Anytype.20260602.174216.86/
       page1.md
       page2.md
       files/
         image.png
         attachment.bin
     Anytype.20260603.100048.72/
       another_page.md
       files/
         ...
   ```
3. Run:
   ```bash
   python notion_import_preprocessor_v1.0.py
   ```
4. Upload the generated `notion_import_*.zip` to Notion via **Import > Text & Markdown**

The script auto-discovers all `Anytype.*` subdirectories and processes each independently.

## Output ZIP Structure

```
notion_import_<title>.zip
  Title1.md          (renamed from original filename using H1)
  Title2.md
  files/
    image.png        (referenced images only)
  README_IMPORT_GUIDE.txt
```

## Requirements

- Python 3.7+ (no external dependencies)
- Works on Windows / macOS / Linux

## License

MIT
