"""Fetch today's Kite access token from sos-kite before each job."""
import logging
import requests
import config

log = logging.getLogger("token_sync")

def refresh():
    if not config.SOS_KITE_URL or not config.TOKEN_SHARE_SECRET:
        log.warning("token_sync not configured")
        return False
    try:
        r = requests.get(f"{config.SOS_KITE_URL}/token",
                         params={"secret": config.TOKEN_SHARE_SECRET}, timeout=10)
        tok = r.json().get("token")
        if tok:
            config.KITE_ACCESS_TOKEN = tok
            log.info("token_sync: fresh token loaded")
            return True
        log.warning("token_sync: sos-kite has no token (morning login not done?)")
    except Exception as e:
        log.warning(f"token_sync failed: {e}")
    return False
