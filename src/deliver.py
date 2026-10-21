"""Delivery. Email is the only route that matters here.

Writing on a PDF with the pen only works for files delivered wirelessly —
a USB sideload gives you a read-only document. So: SMTP to @kindle.com,
from an address on the Approved Personal Document E-mail List.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def send_to_kindle(pdf: Path, subject: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]          # must be an approved sender
    password = os.environ["SMTP_PASS"]      # app password, not your login
    to = os.environ["KINDLE_EMAIL"]         # yourname@kindle.com

    size_mb = pdf.stat().st_size / 1e6
    if size_mb > 49:
        raise RuntimeError(f"{size_mb:.1f} MB exceeds the 50 MB email limit")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject          # becomes the document title in your library
    msg.set_content("The Daily Cronicle attached.")
    msg.add_attachment(pdf.read_bytes(), maintype="application",
                       subtype="pdf", filename=pdf.name)

    with smtplib.SMTP(host, port, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"  delivered {pdf.name} ({size_mb:.1f} MB) to Kindle")
