# pphs (Python Pretty Http Server)

A polished, developer-friendly alternative to `python -m http.server`.

## Features

- Python 3.10+
- Typer CLI
- `ThreadingHTTPServer` + `SimpleHTTPRequestHandler`
- Live filename search and file-type filter
- Click-to-sort columns (name, size, modified date)
- **Drag & drop file upload** straight from the browser (or use the Browse button)
- **QR code in the terminal** for one-tap access from your phone on the same Wi-Fi
- **Light/dark theme toggle**, remembered per browser
- **Breadcrumb navigation** for the current path
- Folder/file counts and total size shown per directory
- **View** and **Download** buttons
- Built-in source viewer with line numbers
- Pygments syntax highlighting
- Markdown rendering with the optional `markdown` extra
- Responsive developer UI
- Unicode-safe filenames, URL encoding, and HTML escaping
- Browser auto-open
- Standard-library static file behavior underneath
- PyPI-ready wheel and source distribution

## Installation

```bash
python -m pip install pphs
```

For Markdown rendering:

```bash
python -m pip install "pphs[markdown]"
```

For development/build tools:

```bash
python -m pip install "pphs[dev]"
```

## Usage

```bash
pphs
pphs ./public
pphs ./public --port 3000
pphs ./public --host 0.0.0.0 --port 8000
pphs ./public --open
pphs ./public --host 0.0.0.0 --no-qr
```

Module form:

```bash
python -m pphs ./public --port 8000
```

### Connecting from your phone

Bind to `0.0.0.0` so devices on the same Wi-Fi can reach the server, e.g.
`pphs ./public --host 0.0.0.0`. A QR code pointing at your machine's LAN
address is printed in the terminal — scan it with your phone's camera to
open the server instantly. Pass `--no-qr` to skip it.

### Uploading files

Every directory page has a drag-and-drop zone (and a **Browse…** button) for
quickly sending files from another device into the folder being served. This
is handy for shuttling files to/from a phone or another computer on the same
network. There's currently no way to disable uploads from the CLI — keep
that in mind before pointing `pphs` at a directory on an untrusted network.

## Viewing files

Supported source/text files are opened in a dedicated viewer:

- Python
- JavaScript / TypeScript
- HTML
- CSS
- JSON
- Markdown
- YAML
- TOML
- SQL
- Shell
- and other formats recognized by Pygments

Markdown files are rendered when `Markdown` is installed; otherwise they
are shown as syntax-highlighted source.

## Security

This is intended for local development. Inline viewing is limited to
recognized text/source formats. File contents are HTML-escaped before being
inserted into the viewer. Static serving remains delegated to
`SimpleHTTPRequestHandler`. Anyone who can reach the server can also **upload
files into any directory being served** (uploaded filenames are sanitized
against path traversal, but there is no authentication). Only run `pphs` on
networks you trust.

## License

MIT
