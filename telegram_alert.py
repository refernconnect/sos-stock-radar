"""sos-stock-radar :: telegram_alert"""
import logging
import requests
import config

log = logging.getLogger("telegram")

def send(text: str):
    if not config.TG_BOT_TOKEN or not config.TG_CHAT_ID:
        log.warning("telegram not configured; message:\n" + text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TG_CHAT_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        log.error(f"telegram send failed: {e}")
