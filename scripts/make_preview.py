import json, re, sys
from pathlib import Path

root = Path("/var/minis/workspace/pocket-desk")
md = (root / "README.md").read_text(encoding="utf-8")

# strip the <p align="center"> wrapper noise for a rough preview, keep structure
html_body = []
in_code = False
code_buf = []
in_html = False
html_html = []

for line in md.splitlines():
    if line.startswith("```"):
        if in_code:
            html_body.append("<pre><code>" + "\n".join(code_buf).replace("<", "&lt;") + "</code></pre>")
            code_buf = []
            in_code = False
        else:
            in_code = True
        continue
    if in_code:
        code_buf.append(line)
        continue

    if in_html:
        # continue a multi-line HTML tag
        html_html.append(line)
        if ">" in line:
            html_body.append("__RAW__" + " ".join(html_html))
            html_html = []
            in_html = False
        continue

    if line.lstrip().startswith("<"):
        stripped = line.strip()
        if ">" not in stripped:
            html_html = [stripped]
            in_html = True
            continue
        html_body.append("__RAW__" + stripped)
        continue

    if line.startswith("# "):
        html_body.append(f"<h1>{line[2:]}</h1>")
    elif line.startswith("## "):
        html_body.append(f"<h2>{line[3:]}</h2>")
    elif line.startswith("### "):
        html_body.append(f"<h3>{line[4:]}</h3>")
    elif line.startswith("---"):
        html_body.append("<hr>")
    elif line.startswith("| "):
        cells = [c.strip() for c in line.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        html_body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    elif re.match(r"^- ", line):
        html_body.append(f"<li>{line[2:]}</li>")
    elif line.strip():
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        html_body.append(f"<p>{text}</p>")

# merge consecutive li / tr
merged = []
for frag in html_body:
    if frag.startswith("<li>") and merged and merged[-1] == "__ul__":
        continue
    if frag.startswith("<li>"):
        merged.append("__ul__")
        merged.append(frag)
    elif frag.startswith("<tr>"):
        merged.append(frag)
    else:
        merged.append(frag)

body = "\n".join(
    x[7:] if x.startswith("__RAW__") else x
    for x in merged if x != "__ul__"
)
# wrap li runs
body = re.sub(r'(?:<li>.*?</li>\n?)+', lambda m: "<ul>" + m.group(0) + "</ul>", body, flags=re.S)
body = re.sub(r'(?:<tr>.*?</tr>\n?)+', lambda m: "<table>" + m.group(0) + "</table>", body, flags=re.S)

# local image refs -> minis:// absolute
body = body.replace('src="./', 'src="minis://workspace/pocket-desk/')
body = body.replace('href="./', 'href="minis://workspace/pocket-desk/')

page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{margin:0;background:#0d1117;color:#e6edf3;font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif}}
 .wrap{{max-width:860px;margin:0 auto;padding:28px 20px 60px}}
 h1{{font-size:2em;border-bottom:1px solid #30363d;padding-bottom:.3em;margin:.6em 0}}
 h2{{font-size:1.5em;border-bottom:1px solid #21262d;padding-bottom:.3em;margin:1.6em 0 .6em}}
 h3{{font-size:1.2em;margin:1.3em 0 .5em}}
 img{{max-width:100%;height:auto;border-radius:8px}}
 a{{color:#58a6ff;text-decoration:none}}
 code{{background:#161b22;padding:.2em .45em;border-radius:4px;font:85%/1.5 ui-monospace,Menlo,monospace;color:#79c0ff}}
 pre{{background:#161b22;padding:14px 16px;border-radius:8px;overflow:auto}}
 pre code{{background:none;padding:0;color:#c9d1d9}}
 table{{border-collapse:collapse;margin:1em 0;width:100%}}
 td{{border:1px solid #30363d;padding:7px 12px}}
 hr{{border:0;border-top:1px solid #30363d;margin:2em 0}}
 ul{{padding-left:1.4em}} li{{margin:.25em 0}}
 p[align]{{text-align:center}}
</style></head><body><div class="wrap">
{body}
</div></body></html>"""

out = root / "preview.html"
out.write_text(page, encoding="utf-8")
print("wrote", out, len(page), "bytes")
