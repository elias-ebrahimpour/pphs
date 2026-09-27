from __future__ import annotations

import socket
from pathlib import Path
from typing import Annotated

import typer

from .server import run_server


def _validate_host(host: str) -> str:
    if not host.strip():
        raise typer.BadParameter("Host cannot be empty.")
    return host


def _validate_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise typer.BadParameter("Port must be between 1 and 65535.")
    return port


def serve(
    directory: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            metavar="DIRECTORY",
            help="Directory to serve. Defaults to the current directory.",
        ),
    ] = Path("."),
    host: Annotated[str, typer.Option("--host", "-H")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p")] = 8000,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the server URL in the default browser.")
    ] = False,
    qr: Annotated[
        bool,
        typer.Option(
            "--qr/--no-qr",
            help="Print a QR code for quick mobile access when the server is reachable on the LAN.",
        ),
    ] = True,
) -> None:
    """Serve DIRECTORY over HTTP."""
    host = _validate_host(host)
    port = _validate_port(port)
    directory = directory.resolve()
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise typer.BadParameter(f"Unable to resolve host {host!r}: {exc}") from exc
    run_server(directory=directory, host=host, port=port, open_browser=open_browser, show_qr=qr)


def main() -> None:
    typer.run(serve)


if __name__ == "__main__":
    main()
