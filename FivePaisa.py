import csv
import sys
from pathlib import Path

from XTS.Connect import XTSConnect


CSV_FILENAME = "FivePaisaCredentials.csv"


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


if __name__ == "__main__":
    main()