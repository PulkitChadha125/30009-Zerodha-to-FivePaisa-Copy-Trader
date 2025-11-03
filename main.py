import csv
import sys
from pathlib import Path

from zerodha_integration import login


CSV_FILENAME = "ZerodhaCredentials.csv"


def read_credentials(csv_path: Path) -> dict:
    creds = {}
    if not csv_path.exists():
        raise FileNotFoundError(f"Credentials file not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            if len(row) < 2:
                continue
            key = (row[0] or "").strip().lower()
            value = (row[1] or "").strip()
            if key:
                creds[key] = value
    return creds


def main() -> None:
    csv_path = Path(__file__).parent / CSV_FILENAME
    try:
        creds = read_credentials(csv_path)
    except Exception as exc:
        print(f"Error reading credentials: {exc}")
        sys.exit(1)

    # Map expected fields (support multiple naming variants)
    def first(*keys: str) -> str | None:
        for k in keys:
            v = creds.get(k)
            if v:
                return v
        return None

    api_key = first("key", "api_key", "zerodhaapikey")
    api_secret = first("secret", "api_secret", "zerodhapisecret")
    request_token = first("request_token")
    user_id = first("id", "userid", "zerodhauserid")
    password = first("pwd", "password", "zerodhapassword")
    totp_secret = first("zerodha2fa", "2fa", "totp", "fa")
    chromedriver_path = first("chromedriver", "chromedriver_path")

    # Validate depending on available inputs
    missing = []
    if not api_key:
        missing.append("api_key (CSV key: 'key' or 'ZerodhaApiKey')")
    if not api_secret:
        missing.append("api_secret (CSV key: 'secret' or 'ZerodhaApiSecret')")

    if request_token:
        pass
    else:
        if not user_id:
            missing.append("user_id (CSV key: 'ID' or 'ZerodhaUserId')")
        if not password:
            missing.append("password (CSV key: 'pwd' or 'ZerodhaPassword')")
        if not totp_secret:
            missing.append("totp_secret (CSV key: 'Zerodha2fa')")

    if missing:
        print("Missing required credential(s):")
        for m in missing:
            print(f" - {m}")
        print("\nAdd the missing values in ZerodhaCredentials.csv using the two-column format 'title,value'.")
        sys.exit(2)

    try:
        if request_token:
            kite, access_token = login(api_key=api_key, api_secret=api_secret, request_token=request_token)
        else:
            kwargs = dict(
                api_key=api_key,
                api_secret=api_secret,
                user_id=user_id,
                password=password,
                totp_secret=totp_secret,
                headless=True,
            )
            if chromedriver_path:
                kwargs["chromedriver_path"] = chromedriver_path
            kite, access_token = login(**kwargs)
        print("Login successful.")
        print(f"Access token: {access_token}")
    except Exception as exc:
        print(f"Login failed: {exc}")
        sys.exit(3)


if __name__ == "__main__":
    main()


