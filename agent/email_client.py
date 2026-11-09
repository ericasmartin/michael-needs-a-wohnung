from __future__ import annotations
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import os


class EmailClient:
    def __init__(self):
        self.address = os.environ["EMAIL_ADDRESS"]
        self.password = os.environ["EMAIL_PASSWORD"]
        self.imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
        self.smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    def send_email(self, to: str, subject: str, body: str, html_body: str | None = None) -> bool:
        msg = MIMEMultipart("alternative")
        msg["From"] = self.address
        msg["To"] = to
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.address, self.password)
                server.sendmail(self.address, to, msg.as_string())
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False

    def read_inbox(self, folder: str = "INBOX", limit: int = 10, search_subject: str | None = None) -> list[dict]:
        messages = []
        try:
            with imaplib.IMAP4_SSL(self.imap_server) as mail:
                mail.login(self.address, self.password)
                mail.select(folder)

                if search_subject:
                    _, data = mail.search(None, f'SUBJECT "{search_subject}"')
                else:
                    _, data = mail.search(None, "ALL")

                msg_ids = data[0].split()
                # Get the most recent messages
                for msg_id in msg_ids[-limit:]:
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    subject = self._decode_header(msg["Subject"])
                    sender = self._decode_header(msg["From"])
                    date = msg["Date"]

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body = payload.decode("utf-8", errors="replace")
                                break
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")

                    messages.append({
                        "subject": subject,
                        "from": sender,
                        "date": date,
                        "body": body[:2000],  # truncate long emails
                    })
        except Exception as e:
            print(f"Failed to read emails: {e}")

        return messages

    @staticmethod
    def _decode_header(value: str | None) -> str:
        if not value:
            return ""
        decoded_parts = decode_header(value)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(part)
        return " ".join(result)
