"""In-memory QR code generation using qrcode and Pillow."""

from __future__ import annotations

from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def generate_qr_png(data: str, box_size: int = 10, border: int = 3) -> bytes:
    """Render QR code for the given string into PNG bytes in memory."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    image = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
