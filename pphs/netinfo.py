"""Helpers for LAN discovery and terminal QR codes."""

from __future__ import annotations

import socket


def get_local_ip() -> str | None:
    """Best-effort guess at this machine's LAN IP address.

    Opens a UDP socket toward a public address without sending any packets,
    just to ask the OS which local interface/IP it would use.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def print_qr(url: str) -> bool:
    """Print a scannable QR code for *url* to the terminal.

    Returns True if a QR code was printed, False if the optional
    ``qrcode`` dependency isn't installed (caller can show a fallback).
    """
    try:
        import qrcode
    except ImportError:
        return False

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    return True
