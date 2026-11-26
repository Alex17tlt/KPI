import logging

import requests

from app.config import settings

log = logging.getLogger("kpi.email")


def send_email(to: str, subject: str, html: str) -> bool:
    """Send via Resend. Without RESEND_API_KEY the message is written to the server log (dev mode)."""
    if not settings.RESEND_API_KEY:
        log.warning("RESEND_API_KEY не задан. Письмо для %s «%s»:\n%s", to, subject, html)
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": settings.EMAIL_FROM, "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        if resp.status_code >= 300:
            log.error("Resend %s: %s", resp.status_code, resp.text[:400])
            return False
        return True
    except requests.RequestException as e:
        log.error("Resend недоступен: %s", e)
        return False
