"""sos-stock-radar :: sectors
Stock -> sector index mapping for the liquid F&O names. Names not mapped fall
back to NIFTY 50 as their RS benchmark (gate still works, just less sharp).
VERIFY: extend this map when the weekly universe pulls in an unmapped symbol —
module_a logs a warning listing unmapped names each morning.
"""

SECTOR_INDEX = {
    "BANK":   "NIFTY BANK",
    "IT":     "NIFTY IT",
    "AUTO":   "NIFTY AUTO",
    "PHARMA": "NIFTY PHARMA",
    "METAL":  "NIFTY METAL",
    "ENERGY": "NIFTY ENERGY",
    "FMCG":   "NIFTY FMCG",
    "REALTY": "NIFTY REALTY",
    "FIN":    "NIFTY FIN SERVICE",
    "PSUBANK": "NIFTY PSU BANK",
    "MEDIA":  "NIFTY MEDIA",
    "INFRA":  "NIFTY INFRA",
}

STOCK_SECTOR = {
    # Banks / Financials
    "HDFCBANK": "BANK", "ICICIBANK": "BANK", "SBIN": "PSUBANK", "AXISBANK": "BANK",
    "KOTAKBANK": "BANK", "INDUSINDBK": "BANK", "BANKBARODA": "PSUBANK",
    "PNB": "PSUBANK", "CANBK": "PSUBANK", "FEDERALBNK": "BANK", "IDFCFIRSTB": "BANK",
    "AUBANK": "BANK", "BANDHANBNK": "BANK",
    "BAJFINANCE": "FIN", "BAJAJFINSV": "FIN", "HDFCLIFE": "FIN", "SBILIFE": "FIN",
    "ICICIPRULI": "FIN", "ICICIGI": "FIN", "SBICARD": "FIN", "CHOLAFIN": "FIN",
    "SHRIRAMFIN": "FIN", "M&MFIN": "FIN", "LICHSGFIN": "FIN", "PFC": "FIN",
    "RECLTD": "FIN", "IRFC": "FIN", "HUDCO": "FIN", "MUTHOOTFIN": "FIN",
    "MANAPPURAM": "FIN", "PAYTM": "FIN", "POLICYBZR": "FIN", "ANGELONE": "FIN",
    "CDSL": "FIN", "BSE": "FIN", "MCX": "FIN", "JIOFIN": "FIN",
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "LTIM": "IT", "COFORGE": "IT", "PERSISTENT": "IT", "MPHASIS": "IT",
    "OFSS": "IT", "KPITTECH": "IT", "TATAELXSI": "IT", "NAUKRI": "IT", "ZOMATO": "IT",
    # Auto
    "MARUTI": "AUTO", "TATAMOTORS": "AUTO", "M&M": "AUTO", "BAJAJ-AUTO": "AUTO",
    "HEROMOTOCO": "AUTO", "EICHERMOT": "AUTO", "TVSMOTOR": "AUTO",
    "ASHOKLEY": "AUTO", "BHARATFORG": "AUTO", "MOTHERSON": "AUTO", "BOSCHLTD": "AUTO",
    "EXIDEIND": "AUTO", "TIINDIA": "AUTO",
    # Pharma / Health
    "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA", "DIVISLAB": "PHARMA",
    "APOLLOHOSP": "PHARMA", "LUPIN": "PHARMA", "AUROPHARMA": "PHARMA",
    "ALKEM": "PHARMA", "ZYDUSLIFE": "PHARMA", "BIOCON": "PHARMA", "MAXHEALTH": "PHARMA",
    "LAURUSLABS": "PHARMA", "GLENMARK": "PHARMA", "MANKIND": "PHARMA",
    # Metals
    "TATASTEEL": "METAL", "JSWSTEEL": "METAL", "HINDALCO": "METAL", "VEDL": "METAL",
    "JINDALSTEL": "METAL", "SAIL": "METAL", "NMDC": "METAL", "NATIONALUM": "METAL",
    "HINDZINC": "METAL", "APLAPOLLO": "METAL",
    # Energy / Oil / Power
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "NTPC": "ENERGY", "POWERGRID": "ENERGY",
    "COALINDIA": "ENERGY", "BPCL": "ENERGY", "IOC": "ENERGY", "GAIL": "ENERGY",
    "ADANIGREEN": "ENERGY", "ADANIPOWER": "ENERGY", "TATAPOWER": "ENERGY",
    "ADANIENSOL": "ENERGY", "JSWENERGY": "ENERGY", "NHPC": "ENERGY", "SJVN": "ENERGY",
    "OIL": "ENERGY", "HINDPETRO": "ENERGY", "PETRONET": "ENERGY", "IGL": "ENERGY",
    # FMCG / Consumer
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "TATACONSUM": "FMCG", "DABUR": "FMCG", "GODREJCP": "FMCG", "MARICO": "FMCG",
    "COLPAL": "FMCG", "UNITDSPR": "FMCG", "VBL": "FMCG", "JUBLFOOD": "FMCG",
    # Realty / Infra / Cement
    "DLF": "REALTY", "GODREJPROP": "REALTY", "OBEROIRLTY": "REALTY",
    "PRESTIGE": "REALTY", "LODHA": "REALTY", "PHOENIXLTD": "REALTY",
    "LT": "INFRA", "ADANIPORTS": "INFRA", "ADANIENT": "INFRA", "GMRINFRA": "INFRA",
    "IRB": "INFRA", "NCC": "INFRA", "RVNL": "INFRA", "IRCON": "INFRA", "NBCC": "INFRA",
    "ULTRACEMCO": "INFRA", "GRASIM": "INFRA", "AMBUJACEM": "INFRA", "SHREECEM": "INFRA",
    "DALBHARAT": "INFRA", "ACC": "INFRA",
    # Others (RS vs NIFTY 50 only)
    "BHARTIARTL": "NIFTY50", "TITAN": "NIFTY50", "ASIANPAINT": "NIFTY50",
    "PIDILITIND": "NIFTY50", "HAL": "NIFTY50", "BEL": "NIFTY50", "BDL": "NIFTY50",
    "MAZDOCK": "NIFTY50", "COCHINSHIP": "NIFTY50", "SIEMENS": "NIFTY50",
    "ABB": "NIFTY50", "CGPOWER": "NIFTY50", "POLYCAB": "NIFTY50", "HAVELLS": "NIFTY50",
    "DIXON": "NIFTY50", "KAYNES": "NIFTY50", "TRENT": "NIFTY50", "DMART": "NIFTY50",
    "INDIGO": "NIFTY50", "IRCTC": "NIFTY50", "CONCOR": "NIFTY50", "DELHIVERY": "NIFTY50",
    "SRF": "NIFTY50", "PIIND": "NIFTY50", "UPL": "NIFTY50", "DEEPAKNTR": "NIFTY50",
    "TATACHEM": "NIFTY50", "ASTRAL": "NIFTY50", "SUPREMEIND": "NIFTY50", "VOLTAS": "NIFTY50",
}

def sector_of(symbol: str) -> str:
    return STOCK_SECTOR.get(symbol, "NIFTY50")

def sector_index_symbol(sector: str) -> str:
    return SECTOR_INDEX.get(sector, "NIFTY 50")
