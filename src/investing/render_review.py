"""Render a review markdown file (with ansi code blocks) to A4 PDF via Chrome headless."""

import re
import subprocess
import sys
from pathlib import Path

import markdown as md
from ansi2html import Ansi2HTMLConverter


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

ANSI_FENCE = re.compile(r"```ansi\n(.*?)```", re.DOTALL)

CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #1a1a1a;
    max-width: 100%;
    padding: 20mm 18mm;
}
h1, h2, h3 { color: #111; margin-top: 1.4em; }
h2 { }
pre {
    font-family: "SF Mono", "Fira Mono", "Consolas", monospace;
    font-size: 8.5pt;
    background: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #333;
    border-radius: 4px;
    padding: 10px 12px;
    overflow-x: auto;
    white-space: pre;
}
code { font-family: "SF Mono", "Fira Mono", "Consolas", monospace; font-size: 0.9em; }
p code, li code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }
hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
table { border-collapse: collapse; width: 100%; font-size: 10pt; }
th, td { border: 1px solid #ddd; padding: 5px 8px; text-align: left; }
th { background: #f5f5f5; }
blockquote { border-left: 3px solid #ccc; margin: 0; padding-left: 1em; color: #555; }
/* ansi2html resets */
.ansi2html-content { display: block; white-space: pre; }
.body_foreground { color: #1a1a1a; }
.body_background { background: #fafafa; }
@media print {
    body { padding: 0; }
    pre { page-break-inside: avoid; }
    h2 { page-break-after: avoid; }
}
"""


_ANSI_RE = re.compile(r"(?<!\x1b)\[([0-9;]*[mKHJABCDEFGSTu])")


def _restore_esc(text: str) -> str:
    """Re-insert ESC bytes stripped by markdown/text editors (bare [3m → \x1b[3m)."""
    return _ANSI_RE.sub(lambda m: f"\x1b[{m.group(1)}", text)


def convert_ansi_block(ansi_text: str, conv: Ansi2HTMLConverter) -> str:
    html = conv.convert(_restore_esc(ansi_text), full=False)
    return f'<pre class="ansi-block">{html}</pre>'


FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def build_html(markdown_path: Path) -> str:
    source = FRONTMATTER.sub("", markdown_path.read_text()).replace(r"\$", "$")
    conv = Ansi2HTMLConverter(inline=True, scheme="xterm")

    # Split on ```ansi fences and process chunks alternately
    parts = ANSI_FENCE.split(source)  # odd indices are the captured ansi content
    html_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            html_parts.append(md.markdown(part, extensions=["tables", "fenced_code"]))
        else:
            html_parts.append(convert_ansi_block(part, conv))

    # Pull ansi2html's inline style sheet
    full_conv_html = conv.convert("", full=True)
    style_match = re.search(r"<style[^>]*>(.*?)</style>", full_conv_html, re.DOTALL)
    ansi_styles = style_match.group(1) if style_match else ""

    title = markdown_path.stem
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
{ansi_styles}
{CSS}
</style>
</head>
<body>
{"".join(html_parts)}
</body>
</html>"""


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: render-review <review.md> [output.html]", file=sys.stderr)
        sys.exit(1)

    md_path = Path(sys.argv[1]).resolve()
    if not md_path.exists():
        print(f"error: {md_path} not found", file=sys.stderr)
        sys.exit(1)

    out_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else md_path.with_suffix(".html")

    html = build_html(md_path)
    out_path.write_text(html)

    subprocess.run(["open", str(out_path)])
    print(out_path)
