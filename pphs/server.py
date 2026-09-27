"""HTTP server and developer-oriented browser UI."""

from __future__ import annotations

import html
import io
import json
import mimetypes
import os
import threading
import webbrowser
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Final
from urllib.parse import quote, unquote, urlsplit

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_for_filename

from .netinfo import get_local_ip, print_qr

try:
    import markdown as markdown_lib
except ImportError:
    markdown_lib = None

HOST_DEFAULT: Final[str] = "127.0.0.1"
PORT_DEFAULT: Final[int] = 8000

_VIEW_PREFIX = "/__pphs/view/"
_DOWNLOAD_PREFIX = "/__pphs/download/"
_UPLOAD_PREFIX = "/__pphs/upload/"
_MAX_UPLOAD_SIZE = 1024 * 1024 * 1024  # 1 GiB safety cap per request

_TEXT_EXTENSIONS = {
    ".txt", ".log", ".ini", ".cfg", ".conf", ".env",
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".jsx", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".jsonc", ".xml", ".svg", ".md", ".markdown", ".mdx",
    ".yaml", ".yml", ".toml", ".sql", ".sh", ".bash", ".zsh",
    ".fish", ".bat", ".cmd", ".ps1", ".rs", ".go", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb", ".swift",
    ".kt", ".kts", ".dart", ".lua", ".r", ".vue", ".svelte",
    ".graphql", ".gql", ".dockerfile",
}

_MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdx"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
_VIEWABLE_BINARY_EXTENSIONS = _IMAGE_EXTENSIONS | _AUDIO_EXTENSIONS | _VIDEO_EXTENSIONS | {".pdf"}

# Shared light/dark theme variables + toggle script, reused across every page.
_THEME_VARS = """
:root{--bg:#0b0d10;--surface:#11151a;--surface2:#171c22;--border:#262d36;--text:#f1f5f9;--muted:#8994a3;--accent:#8ab4ff}
:root[data-theme="light"]{--bg:#f5f6f8;--surface:#ffffff;--surface2:#eef1f5;--border:#dde3ea;--text:#161a20;--muted:#5b6472;--accent:#2f5fdd}
"""

_THEME_TOGGLE_BUTTON = (
    '<button type="button" id="themeToggle" class="btn theme-toggle" '
    'aria-label="Toggle light/dark theme" title="Toggle theme">🌙</button>'
)

# Applied synchronously in <head>, before first paint, to avoid a theme flash.
_THEME_INIT_INLINE = (
    "<script>(function(){var t=localStorage.getItem('pphs-theme');"
    "document.documentElement.setAttribute('data-theme', t==='light'?'light':'dark');})();</script>"
)

# Wires up the toggle button; placed at the end of <body>, after the button exists.
_THEME_TOGGLE_SCRIPT = """
(function(){
  var root=document.documentElement, btn=document.getElementById('themeToggle');
  function sync(){ if(btn) btn.textContent = root.getAttribute('data-theme')==='light' ? '☀️' : '🌙'; }
  sync();
  if(btn){
    btn.addEventListener('click', function(){
      var next = root.getAttribute('data-theme')==='light' ? 'dark' : 'light';
      localStorage.setItem('pphs-theme', next);
      root.setAttribute('data-theme', next);
      sync();
    });
  }
})();
"""


class PPHSRequestHandler(SimpleHTTPRequestHandler):
    """Standard static handler with a custom browser and file viewer."""

    server_version = "PPHSServer/2.0"

    def do_GET(self) -> None:
        if self.path.startswith(_VIEW_PREFIX):
            self._handle_view()
            return
        if self.path.startswith(_DOWNLOAD_PREFIX):
            self._handle_download()
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self.path.startswith(_VIEW_PREFIX):
            self._handle_view(head_only=True)
            return
        if self.path.startswith(_DOWNLOAD_PREFIX):
            self._handle_download(head_only=True)
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        if self.path.startswith(_UPLOAD_PREFIX):
            self._handle_upload()
            return
        self.send_error(501, "Unsupported method (POST)")

    def _handle_upload(self) -> None:
        target_dir = self._special_path_to_filesystem(_UPLOAD_PREFIX)
        if target_dir is None or not target_dir.is_dir():
            self._send_json(404, {"ok": False, "error": "Target directory not found."})
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json(400, {"ok": False, "error": "Expected multipart/form-data."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "Empty upload."})
            return
        if length > _MAX_UPLOAD_SIZE:
            self._send_json(413, {"ok": False, "error": "Upload too large."})
            return

        body = self.rfile.read(length)
        header_bytes = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
            "utf-8"
        )
        message = BytesParser(policy=policy.default).parsebytes(header_bytes + body)

        saved: list[str] = []
        errors: list[str] = []
        if message.is_multipart():
            for part in message.iter_parts():
                filename = part.get_filename()
                if not filename:
                    continue
                safe_name = os.path.basename(filename.replace("\\", "/")).strip()
                if not safe_name or safe_name in {".", ".."}:
                    errors.append(f"Skipped invalid filename: {filename!r}")
                    continue
                destination = (target_dir / safe_name).resolve()
                try:
                    destination.relative_to(target_dir.resolve())
                except ValueError:
                    errors.append(f"Skipped unsafe filename: {filename!r}")
                    continue
                payload = part.get_payload(decode=True) or b""
                try:
                    destination.write_bytes(payload)
                    saved.append(safe_name)
                except OSError as exc:
                    errors.append(f"{safe_name}: {exc}")

        if not saved and errors:
            self._send_json(400, {"ok": False, "error": "; ".join(errors)})
            return
        self._send_json(200, {"ok": True, "saved": saved, "errors": errors})

    def _send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def list_directory(self, path: str):  # type: ignore[override]
        try:
            entries = list(os.scandir(path))
        except OSError:
            self.send_error(404, "No permission to list directory")
            return None

        directories = sorted(
            (e for e in entries if e.is_dir()),
            key=lambda e: e.name.casefold(),
        )
        files = sorted(
            (e for e in entries if not e.is_dir()),
            key=lambda e: e.name.casefold(),
        )

        current_url_path = urlsplit(self.path).path
        if not current_url_path.endswith("/"):
            current_url_path += "/"

        display_path = unquote(current_url_path) or "/"

        items = []
        for entry in directories:
            items.append(self._render_entry(entry, current_url_path, True))

        total_bytes = 0
        for entry in files:
            items.append(self._render_entry(entry, current_url_path, False))
            try:
                total_bytes += entry.stat().st_size
            except OSError:
                pass

        body = self._browser_html(
            display_path=display_path,
            folder_count=len(directories),
            file_count=len(files),
            total_bytes=total_bytes,
            rows="".join(items) if items else self._render_empty(),
        )
        encoded = body.encode("utf-8", "surrogateescape")
        output = io.BytesIO(encoded)
        output.seek(0)

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        return output

    def _render_entry(self, entry: os.DirEntry[str], base_url: str, is_directory: bool) -> str:
        name = entry.name
        href = quote(name) + ("/" if is_directory else "")
        safe_name = html.escape(name, quote=True)
        safe_href = html.escape(href, quote=True)
        icon, kind = self._icon_for(name, is_directory)

        try:
            mtime = entry.stat().st_mtime
        except OSError:
            mtime = 0

        if is_directory:
            actions = ""
            size = "—"
            size_bytes = -1
        else:
            view_url = _VIEW_PREFIX + quote(base_url.lstrip("/"), safe="/") + quote(name)
            download_url = _DOWNLOAD_PREFIX + quote(base_url.lstrip("/"), safe="/") + quote(name)
            actions = (
                f'<span class="actions">'
                f'<a class="btn" href="{html.escape(view_url, quote=True)}">View</a>'
                f'<a class="btn btn-primary" href="{html.escape(download_url, quote=True)}">Download</a>'
                f'</span>'
            )
            try:
                size_bytes = entry.stat().st_size
            except OSError:
                size_bytes = 0
            size = self._format_bytes(size_bytes)

        modified = self._format_modified(entry.path)

        return f"""
        <div class="entry" data-name="{safe_name}" data-kind="{html.escape(kind, quote=True)}" data-size-bytes="{size_bytes}" data-mtime="{mtime}">
          <a class="entry-main" href="{safe_href}" title="{safe_name}">
            <span class="icon">{icon}</span>
            <span class="name">
              <span class="filename">{safe_name}{"/" if is_directory else ""}</span>
              <span class="kind">{html.escape(kind)}</span>
            </span>
          </a>
          <span class="meta size">{html.escape(size)}</span>
          <span class="meta modified">{html.escape(modified)}</span>
          {actions}
        </div>
        """

    @staticmethod
    def _render_empty() -> str:
        return """
        <div class="empty">
          <div class="empty-icon">◌</div>
          <strong>This directory is empty</strong>
          <span>There are no files or folders to display.</span>
        </div>
        """

    def _handle_view(self, head_only: bool = False) -> None:
        path = self._special_path_to_filesystem(_VIEW_PREFIX)
        if path is None:
            self.send_error(404, "File not found")
            return
        if path.is_dir():
            self.send_error(400, "A directory cannot be viewed as a file")
            return
        suffix = path.suffix.lower()
        if suffix in _VIEWABLE_BINARY_EXTENSIONS:
            rendered = self._media_viewer_html(path)
            self._send_viewer_response(rendered, head_only=head_only)
            return

        if suffix not in _TEXT_EXTENSIONS and not path.name.lower().startswith("dockerfile"):
            self.send_error(415, "This file type is not supported by the built-in viewer")
            return

        try:
            raw = path.read_bytes()
        except OSError as exc:
            self.send_error(404, str(exc))
            return

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="replace")

        rendered = self._viewer_html(path, content)
        self._send_viewer_response(rendered, head_only=head_only)

    def _send_viewer_response(self, rendered: str, *, head_only: bool = False) -> None:
        encoded = rendered.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if not head_only:
            self.wfile.write(encoded)

    def _handle_download(self, head_only: bool = False) -> None:
        path = self._special_path_to_filesystem(_DOWNLOAD_PREFIX)
        if path is None or not path.is_file():
            self.send_error(404, "File not found")
            return

        try:
            size = path.stat().st_size
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{self._ascii_filename(path.name)}"',
            )
            self.end_headers()
            if not head_only:
                with path.open("rb") as file:
                    while chunk := file.read(1024 * 128):
                        self.wfile.write(chunk)
        except OSError as exc:
            self.send_error(404, str(exc))

    def _special_path_to_filesystem(self, prefix: str) -> Path | None:
        raw_path = urlsplit(self.path).path
        encoded_relative = raw_path[len(prefix):]
        relative = unquote(encoded_relative).lstrip("/")
        root = Path(self.directory or os.getcwd()).resolve()
        candidate = (root / relative).resolve()

        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _ascii_filename(name: str) -> str:
        safe = name.encode("ascii", "ignore").decode("ascii") or "download"
        return safe.replace('"', "_").replace("\r", "_").replace("\n", "_")

    @staticmethod
    def _parent_url(current: str) -> str:
        parent = current.rstrip("/").rsplit("/", 1)[0] or "/"
        return parent if parent.endswith("/") else parent + "/"

    @staticmethod
    def _format_bytes(size: int) -> str:
        if size < 0:
            return "—"
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{int(value):,} B" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return "—"

    @staticmethod
    def _breadcrumbs(display_path: str) -> str:
        segments = [seg for seg in display_path.strip("/").split("/") if seg]
        crumbs = ['<a class="crumb" href="/">🏠 Home</a>']
        accumulated = ""
        for index, segment in enumerate(segments):
            accumulated += "/" + quote(segment)
            safe_label = html.escape(segment)
            if index == len(segments) - 1:
                crumbs.append(f'<span class="crumb current" aria-current="page">{safe_label}</span>')
            else:
                href = html.escape(accumulated + "/", quote=True)
                crumbs.append(f'<a class="crumb" href="{href}">{safe_label}</a>')
        return '<span class="crumb-sep">/</span>'.join(crumbs)

    @staticmethod
    def _format_modified(path: str) -> str:
        try:
            return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return "—"

    @staticmethod
    def _icon_for(name: str, is_directory: bool) -> tuple[str, str]:
        if is_directory:
            return "📁", "Folder"
        suffix = Path(name).suffix.lower()
        filename = name.lower()
        mapping = {
            ".html": ("🌐", "HTML"), ".htm": ("🌐", "HTML"),
            ".css": ("🎨", "CSS"),
            ".js": ("⚡", "JavaScript"), ".mjs": ("⚡", "JavaScript"), ".cjs": ("⚡", "JavaScript"),
            ".ts": ("🔷", "TypeScript"), ".tsx": ("🔷", "TypeScript"),
            ".py": ("🐍", "Python"), ".pyw": ("🐍", "Python"),
            ".json": ("{}", "JSON"), ".jsonc": ("{}", "JSON"),
            ".md": ("📝", "Markdown"), ".markdown": ("📝", "Markdown"), ".mdx": ("📝", "Markdown"),
            ".yaml": ("🔧", "YAML"), ".yml": ("🔧", "YAML"),
            ".toml": ("⚙️", "TOML"), ".sql": ("🗃️", "SQL"),
            ".png": ("🖼️", "Image"), ".jpg": ("🖼️", "Image"), ".jpeg": ("🖼️", "Image"),
            ".gif": ("🖼️", "Image"), ".webp": ("🖼️", "Image"), ".svg": ("🖼️", "SVG"),
            ".mp3": ("🎵", "Audio"), ".wav": ("🎵", "Audio"),
            ".mp4": ("🎬", "Video"), ".webm": ("🎬", "Video"),
            ".zip": ("📦", "Archive"), ".tar": ("📦", "Archive"), ".gz": ("📦", "Archive"),
        }
        if filename.startswith("dockerfile"):
            return "🐳", "Docker"
        return mapping.get(suffix, ("📄", "File"))


    def _media_viewer_html(self, path: Path) -> str:
        title = html.escape(path.name, quote=True)
        relative = self._relative_url_for_file(path)
        asset_url = "/" + quote(relative, safe="/:@-._~")
        download_url = _DOWNLOAD_PREFIX + quote(relative, safe="/:@-._~")
        suffix = path.suffix.lower()

        if suffix in _IMAGE_EXTENSIONS:
            viewer = f'<img class="media image" src="{html.escape(asset_url, quote=True)}" alt="{title}">'
        elif suffix in _AUDIO_EXTENSIONS:
            viewer = f'<audio class="media audio" controls preload="metadata" src="{html.escape(asset_url, quote=True)}"></audio>'
        elif suffix in _VIDEO_EXTENSIONS:
            viewer = f'<video class="media video" controls preload="metadata" src="{html.escape(asset_url, quote=True)}"></video>'
        else:
            viewer = f'<iframe class="pdf" src="{html.escape(asset_url, quote=True)}" title="{title}"></iframe>'

        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><title>{title} — PPHS</title>
{_THEME_INIT_INLINE}
<style>
{_THEME_VARS}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}}
.top{{position:sticky;top:0;background:var(--bg);opacity:.98;backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}}
.toolbar{{width:min(1200px,calc(100% - 28px));margin:auto;min-height:62px;display:flex;gap:12px;align-items:center}}
.back,.btn{{color:var(--text);text-decoration:none}}.back{{color:var(--muted)}}.title{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}}
.btn{{border:1px solid var(--border);background:var(--surface);border-radius:8px;padding:7px 11px;font-size:13px;cursor:pointer}}
.content{{width:min(1200px,calc(100% - 28px));margin:24px auto 60px}}
.stage{{min-height:60vh;display:grid;place-items:center;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:28px;overflow:auto}}
.image{{max-width:100%;max-height:78vh;object-fit:contain;border-radius:8px}}
.audio{{width:min(700px,100%)}}.video{{max-width:100%;max-height:78vh}}
.pdf{{width:100%;height:78vh;border:0;border-radius:8px;background:white}}
</style></head><body>
<header class="top"><div class="toolbar"><a class="back" href="{html.escape(self._browser_parent_for_file(path),quote=True)}">← Directory</a>
<div class="title">{title}</div>{_THEME_TOGGLE_BUTTON}<a class="btn" href="{html.escape(download_url,quote=True)}">Download</a></div></header>
<main class="content"><section class="stage">{viewer}</section></main>
<script>{_THEME_TOGGLE_SCRIPT}</script>
</body></html>"""

    @staticmethod
    def _highlight_source(path: Path, content: str) -> str:
        try:
            lexer = get_lexer_for_filename(path.name, stripall=False)
        except Exception:
            lexer = TextLexer()
        return highlight(
            content,
            lexer,
            HtmlFormatter(nowrap=True, cssclass="highlight"),
        )

    def _viewer_html(self, path: Path, content: str) -> str:
        highlighted = self._highlight_source(path, content)
        title = html.escape(path.name, quote=True)
        parent_url = self._browser_parent_for_file(path)

        markdown_html = ""
        if path.suffix.lower() in _MARKDOWN_EXTENSIONS and markdown_lib is not None:
            markdown_html = markdown_lib.markdown(
                content,
                extensions=["fenced_code", "tables", "toc"],
                output_format="html5",
            )

        css = HtmlFormatter(style="monokai").get_style_defs(".highlight")
        lines = content.count("\n") + 1

        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>{title} — PPHS</title>
{_THEME_INIT_INLINE}
<style>
{_THEME_VARS}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}}
.top{{position:sticky;top:0;z-index:5;background:var(--bg);opacity:.98;backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}}
.toolbar{{width:min(1400px,calc(100% - 28px));margin:auto;min-height:64px;display:flex;align-items:center;gap:12px}}
.back{{text-decoration:none;color:var(--muted);font-size:14px}}
.title{{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}}
.badge{{font:600 11px ui-monospace,monospace;color:var(--muted);padding:5px 8px;border:1px solid var(--border);border-radius:7px}}
.btn{{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;border:1px solid var(--border);border-radius:8px;padding:7px 11px;color:var(--text);font-size:13px;background:var(--surface);cursor:pointer}}
.btn:hover{{border-color:var(--accent)}}
.content{{width:min(1400px,calc(100% - 28px));margin:22px auto 60px}}
.preview{{background:#0e1115;border:1px solid var(--border);border-radius:14px;overflow:auto}}
.markdown{{padding:34px;line-height:1.75;max-width:900px;margin:auto;color:#eef2f7}}
.markdown h1,.markdown h2,.markdown h3{{line-height:1.2}}
.markdown code{{background:#1b222b;padding:2px 5px;border-radius:5px}}
.markdown pre{{background:#0a0d10;padding:16px;border-radius:10px;overflow:auto}}
.markdown a{{color:var(--accent)}}
.code{{display:grid;grid-template-columns:auto minmax(0,1fr);min-width:max-content;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
.linenos{{user-select:none;color:#4f5b68;text-align:right;padding:18px 14px 18px 18px;border-right:1px solid #262d36;background:#0a0d10}}
.linenos span{{display:block}}
.highlight{{padding:18px 20px;overflow:visible}}
{css}
@media(max-width:700px){{.badge{{display:none}}.toolbar{{width:calc(100% - 18px)}}.content{{width:calc(100% - 18px)}}.markdown{{padding:20px}}}}
</style>
</head>
<body>
<header class="top"><div class="toolbar">
<a class="back" href="{html.escape(parent_url,quote=True)}">← Directory</a>
<div class="title">{title}</div>
<span class="badge">{lines:,} lines</span>
{_THEME_TOGGLE_BUTTON}
<a class="btn" href="{html.escape(_DOWNLOAD_PREFIX + quote(self._relative_url_for_file(path), safe="/"),quote=True)}">Download</a>
</div></header>
<main class="content">
{"<article class='preview markdown'>" + markdown_html + "</article>" if markdown_html else
"<section class='preview code'><div class='linenos'>" + "".join(f"<span>{i}</span>" for i in range(1, lines+1)) + "</div><pre class='highlight'>" + highlighted + "</pre></section>"}
</main>
<script>{_THEME_TOGGLE_SCRIPT}</script>
</body></html>"""

    def _browser_parent_for_file(self, path: Path) -> str:
        relative = self._relative_url_for_file(path)
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        return "/" + parent + ("/" if parent else "")

    def _relative_url_for_file(self, path: Path) -> str:
        root = Path(self.directory or os.getcwd()).resolve()
        relative = path.resolve().relative_to(root).as_posix()
        return relative


    def _browser_html(
        self, *, display_path: str, folder_count: int, file_count: int, total_bytes: int, rows: str
    ) -> str:
        safe_path = html.escape(display_path, quote=True)
        breadcrumbs = self._breadcrumbs(display_path)
        item_count = folder_count + file_count
        summary = (
            f"{folder_count:,} folder{'s' if folder_count != 1 else ''}, "
            f"{file_count:,} file{'s' if file_count != 1 else ''} · "
            f"{self._format_bytes(total_bytes)} total"
        )
        upload_url = _UPLOAD_PREFIX + quote(display_path.lstrip("/"), safe="/")
        template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
__THEME_INIT__
<title>Directory — __PATH__</title>
<style>
__THEME_VARS__
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at top,rgba(80,110,160,.12),transparent 34rem),var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}
.shell{width:min(1180px,calc(100% - 28px));margin:auto;padding:40px 0 70px}
.brandrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{color:var(--accent);font:700 11px ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}
.theme-toggle{border-radius:9px;padding:7px 10px;font-size:15px;line-height:1}
h1{margin:14px 0 0;font-size:clamp(1.7rem,4.4vw,2.7rem);letter-spacing:-.045em}
.breadcrumbs{margin-top:12px;font:13px ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.crumb{color:var(--muted);text-decoration:none}
.crumb:hover{color:var(--accent)}
.crumb.current{color:var(--text);font-weight:600}
.crumb-sep{color:var(--border)}
.toolbar{display:flex;gap:10px;margin:24px 0 14px;flex-wrap:wrap}
input,select{background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:10px 12px;outline:0}
input{flex:1;min-width:220px}
input:focus,select:focus{border-color:var(--accent)}
.count{color:var(--muted);font-size:13px;margin-bottom:12px}
.dropzone{border:2px dashed var(--border);border-radius:14px;padding:16px;margin-bottom:16px;display:flex;align-items:center;justify-content:center;gap:10px;color:var(--muted);font-size:13px;transition:.15s}
.dropzone.drag{border-color:var(--accent);color:var(--text);background:rgba(138,180,255,.06)}
.upload-status{font-size:13px;color:var(--muted);margin:-6px 0 14px}
.upload-status.hidden{display:none}
.browser{overflow:hidden;background:rgba(17,21,26,.9);border:1px solid var(--border);border-radius:16px}
:root[data-theme="light"] .browser{background:rgba(255,255,255,.9)}
.columns,.entry{display:grid;grid-template-columns:minmax(0,1fr) 110px 150px 180px;gap:14px;align-items:center}
.columns{padding:10px 16px;color:var(--muted);font:700 10px ui-monospace,monospace;text-transform:uppercase;border-bottom:1px solid var(--border)}
.columns span[data-sort]{cursor:pointer;user-select:none}
.columns span[data-sort]:hover{color:var(--text)}
.columns span[data-sort].active{color:var(--accent)}
.entry{padding:11px 16px;border-bottom:1px solid var(--border)}
.entry:last-child{border:0}
.entry:hover{background:rgba(138,180,255,.07)}
.entry-main{display:grid;grid-template-columns:34px minmax(0,1fr);gap:12px;align-items:center;text-decoration:none;min-width:0}
.icon{display:grid;place-items:center;width:34px;height:34px;border-radius:9px;background:var(--surface2)}
.filename{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:650;font-size:14px;color:var(--text)}
.kind,.meta{color:var(--muted);font-size:11px}
.kind{margin-top:2px}
.meta{font-family:ui-monospace,monospace}
.actions{display:flex;gap:7px;justify-content:flex-end}
.btn{text-decoration:none;border:1px solid var(--border);border-radius:7px;padding:6px 9px;font-size:11px;color:var(--text);background:var(--surface);cursor:pointer}
.btn:hover{border-color:var(--accent)}
.btn-primary{border-color:#37527a;background:rgba(138,180,255,.09)}
.hidden{display:none}
.empty{padding:50px;text-align:center;color:var(--muted)}
@media(max-width:800px){
  .columns{display:none}
  .entry{grid-template-columns:minmax(0,1fr) auto;padding:12px}
  .meta{display:none}
  .actions{grid-column:2;grid-row:1}
  .filename{white-space:normal;overflow-wrap:anywhere}
}
@media(max-width:500px){
  .shell{padding-top:30px}
  .actions .btn{font-size:0;padding:8px}
  .actions .btn:first-child::before{content:"👁";font-size:13px}
  .actions .btn-primary::before{content:"↓";font-size:14px}
}
</style>
</head>
<body>
<main class="shell">
<div class="brandrow"><span class="brand">PPHS</span>__THEME_BTN__</div>
<h1>Directory</h1>
<nav class="breadcrumbs" aria-label="Breadcrumb">__CRUMBS__</nav>
<div class="toolbar">
<input id="search" type="search" placeholder="Search files and folders…" autocomplete="off">
<select id="type">
<option value="">All types</option>
<option>Folder</option><option>Python</option><option>JavaScript</option><option>TypeScript</option>
<option>HTML</option><option>CSS</option><option>JSON</option><option>Markdown</option>
<option>YAML</option><option>TOML</option><option>SQL</option><option>Image</option>
<option>Audio</option><option>Video</option><option>Archive</option><option>Docker</option><option>File</option>
</select>
</div>
<div id="dropzone" class="dropzone">
<input type="file" id="fileInput" multiple hidden>
<span>📤 Drag &amp; drop files here to upload, or <button type="button" id="browseBtn" class="btn">Browse…</button></span>
</div>
<div id="uploadStatus" class="upload-status hidden"></div>
<div id="count" class="count">__SUMMARY__</div>
<section class="browser">
<div class="columns">
<span data-sort="name" class="active">Name</span>
<span data-sort="size">Size</span>
<span data-sort="modified">Modified</span>
<span>Actions</span>
</div>
<div id="items">__ROWS__</div>
</section>
</main>
<script>
const search=document.querySelector('#search'), type=document.querySelector('#type'),
itemsBox=document.querySelector('#items'), count=document.querySelector('#count');
const baseSummary=__SUMMARY_JSON__;
function items(){ return [...itemsBox.querySelectorAll('.entry')]; }
function filter(){
 const q=search.value.trim().toLowerCase(), t=type.value; let n=0;
 items().forEach(el=>{
   const name=(el.dataset.name||'').toLowerCase(), kind=el.dataset.kind||'';
   const ok=(!q||name.includes(q))&&(!t||kind===t);
   el.classList.toggle('hidden',!ok); if(ok)n++;
 });
 count.textContent = (q||t) ? (n+' matching item'+(n===1?'':'s')) : baseSummary;
}
search.addEventListener('input',filter); type.addEventListener('change',filter);

let sortKey='name', sortDir=1;
function applySort(){
  const arr=items();
  arr.sort((a,b)=>{
    let av,bv;
    if(sortKey==='size'){av=+a.dataset.sizeBytes;bv=+b.dataset.sizeBytes;}
    else if(sortKey==='modified'){av=+a.dataset.mtime;bv=+b.dataset.mtime;}
    else {av=(a.dataset.name||'').toLowerCase();bv=(b.dataset.name||'').toLowerCase();}
    if(av<bv) return -1*sortDir;
    if(av>bv) return 1*sortDir;
    return 0;
  });
  arr.forEach(el=>itemsBox.appendChild(el));
}
document.querySelectorAll('.columns [data-sort]').forEach(el=>{
  el.addEventListener('click',()=>{
    const key=el.dataset.sort;
    if(sortKey===key) sortDir*=-1; else {sortKey=key; sortDir=1;}
    document.querySelectorAll('.columns [data-sort]').forEach(s=>s.classList.remove('active'));
    el.classList.add('active');
    applySort();
  });
});

const dropzone=document.querySelector('#dropzone'), fileInput=document.querySelector('#fileInput'),
browseBtn=document.querySelector('#browseBtn'), uploadStatus=document.querySelector('#uploadStatus');
const uploadUrl='__UPLOAD_URL__';
function uploadFiles(fileList){
  const files=[...fileList];
  if(!files.length) return;
  const fd=new FormData();
  files.forEach(f=>fd.append('file', f, f.name));
  uploadStatus.textContent='Uploading '+files.length+' file'+(files.length===1?'':'s')+'…';
  uploadStatus.classList.remove('hidden');
  fetch(uploadUrl,{method:'POST',body:fd})
    .then(r=>r.json())
    .then(data=>{
      if(data.ok){
        uploadStatus.textContent='Uploaded '+data.saved.length+' file'+(data.saved.length===1?'':'s')+'. Refreshing…';
        setTimeout(()=>location.reload(),600);
      } else {
        uploadStatus.textContent='Upload failed: '+(data.error||'unknown error');
      }
    })
    .catch(err=>{ uploadStatus.textContent='Upload failed: '+err; });
}
browseBtn.addEventListener('click',()=>fileInput.click());
fileInput.addEventListener('change',()=>uploadFiles(fileInput.files));
['dragenter','dragover'].forEach(ev=>dropzone.addEventListener(ev,e=>{e.preventDefault();dropzone.classList.add('drag');}));
['dragleave','drop'].forEach(ev=>dropzone.addEventListener(ev,e=>{e.preventDefault();dropzone.classList.remove('drag');}));
dropzone.addEventListener('drop',e=>uploadFiles(e.dataTransfer.files));

__THEME_SCRIPT__
</script>
</body>
</html>"""
        return (
            template
            .replace("__THEME_INIT__", _THEME_INIT_INLINE)
            .replace("__THEME_VARS__", _THEME_VARS)
            .replace("__THEME_BTN__", _THEME_TOGGLE_BUTTON)
            .replace("__THEME_SCRIPT__", _THEME_TOGGLE_SCRIPT)
            .replace("__PATH__", safe_path)
            .replace("__CRUMBS__", breadcrumbs)
            .replace("__SUMMARY__", html.escape(summary))
            .replace("__SUMMARY_JSON__", json.dumps(summary))
            .replace("__UPLOAD_URL__", html.escape(upload_url, quote=True))
            .replace("__ROWS__", rows)
        )



class PPHSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server(
    *,
    directory: Path,
    host: str = HOST_DEFAULT,
    port: int = PORT_DEFAULT,
    open_browser: bool = False,
    show_qr: bool = True,
) -> None:
    handler = lambda *args, **kwargs: PPHSRequestHandler(
        *args, directory=str(directory), **kwargs
    )
    server = PPHSServer((host, port), handler)
    actual_port = int(server.server_address[1])
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{display_host}:{actual_port}/"

    print(f"Serving: {directory}")
    print(f"Address: {url}")

    if show_qr:
        if host in ("127.0.0.1", "localhost", "::1"):
            print("Tip: pass --host 0.0.0.0 to also serve your LAN and get a scannable QR code.")
        else:
            lan_ip = host if host != "0.0.0.0" else get_local_ip()
            if lan_ip:
                lan_url = f"http://{lan_ip}:{actual_port}/"
                print(f"On your phone (same Wi-Fi): {lan_url}")
                if not print_qr(lan_url):
                    print("(install the 'qrcode' package for a scannable QR code: pip install qrcode)")
            else:
                print("Couldn't detect a LAN IP for a QR code; check your network connection.")

    print("Press Ctrl+C to stop.")

    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.shutdown()
        server.server_close()
        print("Server stopped.")
