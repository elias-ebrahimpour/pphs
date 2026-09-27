from pathlib import Path
from urllib.request import urlopen, Request
from http.server import ThreadingHTTPServer
import threading

from pphs.server import PPHSRequestHandler


def make_server(root: Path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: PPHSRequestHandler(*args, directory=str(root), **kwargs),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_directory_view_and_static(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
    (tmp_path / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# Notes\n\nHello", encoding="utf-8")

    # A separate directory without index tests the custom browser.
    browser_dir = tmp_path / "browser"
    browser_dir.mkdir()
    (browser_dir / "app.py").write_text("print('hello')", encoding="utf-8")

    server, thread = make_server(tmp_path)
    port = server.server_address[1]
    try:
        html = urlopen(f"http://127.0.0.1:{port}/browser/", timeout=3).read().decode()
        assert "app.py" in html
        assert "Search files" in html
        assert "Download" in html
        assert "View" in html

        source = urlopen(
            f"http://127.0.0.1:{port}/__pphs/view/browser/app.py", timeout=3
        ).read().decode()
        assert "app.py" in source
        assert "hello" in source

        downloaded = urlopen(
            f"http://127.0.0.1:{port}/__pphs/download/browser/app.py", timeout=3
        ).read().decode()
        assert "print('hello')" in downloaded

        static = urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3).read().decode()
        assert "<h1>Hello</h1>" in static

        head = urlopen(Request(
            f"http://127.0.0.1:{port}/index.html", method="HEAD"
        ), timeout=3)
        assert head.status == 200
    finally:
        server.shutdown()
        server.server_close()
