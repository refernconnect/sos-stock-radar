"""sos-stock-radar :: nse_data
All NSE-sourced regulatory/EOD data isolated here. If NSE changes a URL,
patch this file only. Every function fails soft (returns None/empty) and logs.

VERIFY ON FIRST RUN: each URL below against a browser download. NSE rotates
formats roughly yearly. Current formats are the UDiFF bhavcopies (2024+).
"""
import io
import zipfile
import logging
import datetime as dt
import requests
import pandas as pd

log = logging.getLogger("nse")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

def _session():
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=10)   # cookie handshake
    except Exception:
        pass
    return s

def _get(url, s=None, timeout=20):
    s = s or _session()
    r = s.get(url, timeout=timeout)
    r.raise_for_status()
    return r

# ---------------------------------------------------------------- bhavcopies
def fo_bhavcopy(d: dt.date) -> pd.DataFrame | None:
    """F&O UDiFF bhavcopy. Columns of interest:
    TckrSymb, FinInstrmTp (STF=stock fut), XpryDt, OpnPric..ClsPric,
    TtlTradgVol, TtlTrfVal, OpnIntrst, ChngInOpnIntrst
    """
    url = (f"https://nsearchives.nseindia.com/content/fo/"
           f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    try:
        r = _get(url)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return pd.read_csv(z.open(z.namelist()[0]))
    except Exception as e:
        log.warning(f"fo_bhavcopy {d} failed: {e}")
        return None

def cm_bhavcopy(d: dt.date) -> pd.DataFrame | None:
    """Cash market UDiFF bhavcopy (OHLCV per symbol)."""
    url = (f"https://nsearchives.nseindia.com/content/cm/"
           f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    try:
        r = _get(url)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = pd.read_csv(z.open(z.namelist()[0]))
        return df[df["SctySrs"] == "EQ"] if "SctySrs" in df.columns else df
    except Exception as e:
        log.warning(f"cm_bhavcopy {d} failed: {e}")
        return None

def delivery_data(d: dt.date) -> pd.DataFrame | None:
    """sec_bhavdata_full: has DELIV_PER column. Whitespace-padded CSV."""
    url = (f"https://nsearchives.nseindia.com/products/content/"
           f"sec_bhavdata_full_{d:%d%m%Y}.csv")
    try:
        r = _get(url)
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        for c in df.select_dtypes("object").columns:
            df[c] = df[c].astype(str).str.strip()
        return df[df["SERIES"] == "EQ"]
    except Exception as e:
        log.warning(f"delivery_data {d} failed: {e}")
        return None

# ---------------------------------------------------------------- regulatory
def ban_list() -> list[str]:
    """Today's F&O ban list. Empty list on failure (module_a treats failure
    as 'unknown' and re-checks; it does NOT assume no bans)."""
    url = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
    try:
        r = _get(url)
        df = pd.read_csv(io.StringIO(r.text), skiprows=1, header=None)
        return [str(x).strip() for x in df[df.columns[-1]].dropna().tolist()]
    except Exception as e:
        log.warning(f"ban_list failed: {e}")
        return []

def mwpl() -> pd.DataFrame | None:
    """Combined OI / MWPL utilisation. VERIFY URL — NSE moves this file.
    Expected columns include symbol and % of MWPL utilised."""
    url = ("https://nsearchives.nseindia.com/content/nsccl/"
           "nseoi_{:%d%m%Y}.zip".format(dt.date.today()))
    try:
        r = _get(url)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return pd.read_csv(z.open(z.namelist()[0]))
    except Exception as e:
        log.warning(f"mwpl failed: {e}")
        return None

def results_today() -> set[str]:
    """Symbols with board-meeting/results events today (NSE event calendar API)."""
    url = "https://www.nseindia.com/api/event-calendar"
    try:
        s = _session()
        r = s.get(url, timeout=15)
        data = r.json()
        today = dt.date.today().strftime("%d-%b-%Y")
        out = set()
        for row in data:
            if row.get("date", "").startswith(today):
                purpose = (row.get("purpose") or "").lower()
                if "result" in purpose or "financial" in purpose:
                    out.add(row.get("symbol", "").strip())
        return out
    except Exception as e:
        log.warning(f"results_today failed: {e}")
        return set()
