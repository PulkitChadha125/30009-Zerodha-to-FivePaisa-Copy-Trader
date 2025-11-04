import csv
import sys
from pathlib import Path

from XTS.Connect import XTSConnect


CSV_FILENAME = "FivePaisaCredentials.csv"


def parse_zerodha_option_symbol(sym: str) -> dict:
    # Example: NIFTY25N0425800PE -> symbol=NIFTY, yy=25, mon_code=N, dd=04, strike=25800, type=PE
    # Build components
    i = 0
    while i < len(sym) and sym[i].isalpha():
        i += 1
    underlying = sym[:i]
    rest = sym[i:]
    # Year (2), month letter (1), day (2)
    yy = rest[:2]
    mon_code = rest[2]
    dd = rest[3:5]
    tail = rest[5:]
    # Strike (digits before CE/PE), type (CE/PE)
    opt_type = "PE" if tail.endswith("PE") else "CE"
    strike_str = tail[:-2]
    strike = int(strike_str)
    # Month letter -> month short
    mon_map = {
        'J': 'Jan', 'F': 'Feb', 'M': 'Mar', 'A': 'Apr', 'Y': 'May', 'H': 'Jun',
        'G': 'Jul', 'U': 'Aug', 'S': 'Sep', 'O': 'Oct', 'N': 'Nov', 'D': 'Dec'
    }
    if mon_code not in mon_map:
        raise ValueError(f"Unknown month code: {mon_code} in {sym}")
    mon = mon_map[mon_code]
    yyyy = f"20{yy}"
    expiry_api_format = f"{dd}{mon}{yyyy}"
    # Series
    idx_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
    series = "OPTIDX" if underlying in idx_symbols else "OPTSTK"
    return {
        "symbol": underlying,
        "expiry_api_format": expiry_api_format,
        "option_type": opt_type,
        "strike": strike,
        "series": series,
    }

def read_credentials(csv_path: Path) -> dict:
    creds = {}
    if not csv_path.exists():
        return creds
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 2:
                continue
            k = (row[0] or "").strip().lower()
            v = (row[1] or "").strip()
            if k:
                creds[k] = v
    return creds


def first(creds: dict, *keys: str) -> str | None:
    for k in keys:
        v = creds.get(k)
        if v:
            return v
    return None


def main() -> None:
    creds = read_credentials(Path(__file__).parent / CSV_FILENAME)

    # Read credentials (support multiple key names)
    interactive_key = first(creds, "interactive_api_key", "interactive key", "interactive_key")
    interactive_secret = first(creds, "interactive_api_secret", "interactive secret", "interactive_secret")
    market_key = first(creds, "market_data_api_key", "market key", "market_api_key")
    market_secret = first(creds, "market_data_api_secret_key", "market secret", "market_api_secret")
    source = first(creds, "source") or "WEBAPI"

    missing = []
    if not interactive_key:
        missing.append("interactive_api_key")
    if not interactive_secret:
        missing.append("interactive_api_secret")
    if not market_key:
        missing.append("market_data_api_key")
    if not market_secret:
        missing.append("market_data_api_secret_key")
    if missing:
        print("Missing required credential(s) in FivePaisaCredentials.csv:")
        for m in missing:
            print(f" - {m}")
        sys.exit(2)

    # Interactive login (trading)
    xt = XTSConnect(apiKey=interactive_key, secretKey=interactive_secret, source=source)
    iresp = xt.interactive_login()
    print("Interactive login response:", iresp)

    # Market data login (quotes)
    xm = XTSConnect(apiKey=market_key, secretKey=market_secret, source=source)
    mresp = xm.marketdata_login()
    print("Marketdata login response:", mresp)

    # Quick test: resolve a Zerodha-style option symbol to 5paisa instrument using get_option_symbol
    test_sym = "NIFTY25N0425800PE"  # adjust as needed
    try:
        parsed = parse_zerodha_option_symbol(test_sym)
        print("Parsed:", parsed)
        resp = xm.get_option_symbol(
            exchangeSegment=2,
            series=parsed["series"],
            symbol=parsed["symbol"],
            expiryDate=parsed["expiry_api_format"],
            optionType=parsed["option_type"],
            strikePrice=parsed["strike"],
        )
        print("5paisa get_option_symbol response:", resp)
        # Log
        log_path = Path(__file__).parent / "Orderlog.txt"
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(f"RESOLVE {test_sym} -> {resp}\n")
    except Exception as e:
        print(f"Symbol resolve failed: {e}")


if __name__ == "__main__":
    main()

def parse_zerodha_option_symbol(sym: str) -> dict:
    # Example: NIFTY25N0425800PE -> symbol=NIFTY, yy=25, mon_code=N, dd=04, strike=25800, type=PE
    # Build components
    # 1) Extract underlying alpha prefix
    i = 0
    while i < len(sym) and sym[i].isalpha():
        i += 1
    underlying = sym[:i]
    rest = sym[i:]
    # 2) Year (2), month letter (1), day (2)
    yy = rest[:2]
    mon_code = rest[2]
    dd = rest[3:5]
    tail = rest[5:]
    # 3) Strike (digits before CE/PE), type (CE/PE)
    opt_type = "PE" if tail.endswith("PE") else "CE"
    strike_str = tail[:-2]
    strike = int(strike_str)
    # Month letter -> month short
    mon_map = {
        'N': 'Nov', 'D': 'Dec', 'O': 'Oct', 'S': 'Sep', 'A': 'Apr', 'M': 'Mar',
        'J': 'Jan', 'F': 'Feb', 'U': 'Aug', 'G': 'Jul', 'H': 'Jun', 'Y': 'May'
    }
    if mon_code not in mon_map:
        raise ValueError(f"Unknown month code: {mon_code} in {sym}")
    mon = mon_map[mon_code]
    yyyy = f"20{yy}"
    expiry_api_format = f"{dd}{mon}{yyyy}"
    # Series
    idx_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
    series = "OPTIDX" if underlying in idx_symbols else "OPTSTK"
    return {
        "symbol": underlying,
        "expiry_api_format": expiry_api_format,
        "option_type": opt_type,
        "strike": strike,
        "series": series,
    }