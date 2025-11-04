import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from kiteconnect import KiteConnect

from zerodha_integration import login as z_login
from XTS.Connect import XTSConnect


Z_CRED_CSV = "ZerodhaCredentials.csv"
FP_CRED_CSV = "FivePaisaCredentials.csv"
MAPPING_FILE = "copy_map.json"
ORDER_LOG = "Orderlog.txt"


def read_csv_kv(csv_path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not csv_path.exists():
        return out
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or len(row) < 2:
                continue
            k = (row[0] or "").strip().lower()
            v = (row[1] or "").strip()
            if k:
                out[k] = v
    return out


def log_line(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        Path(ORDER_LOG).open("a", encoding="utf-8").write(line)
    except Exception:
        pass
    print(line, end="")


def load_mapping(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"orders": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}") or {"orders": {}}
    except Exception:
        return {"orders": {}}


def save_mapping(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_multiplier(z_creds: Dict[str, str]) -> int:
    # Accept several key variants
    for key in ["copytradeqtymultiplier", "copy_trade_qty_multiplier", "multiplier"]:
        if key in z_creds:
            try:
                val = int(float(z_creds[key]))
                return max(1, val)
            except Exception:
                continue
    return 1


def resolve_5p_instrument(xm: XTSConnect, exch: str, tradingsymbol: str) -> Optional[Dict[str, Any]]:
    # Very basic resolver: handle index options like NIFTY25N0425800PE via get_option_symbol
    try:
        # Try parse as option
        parsed = parse_zerodha_option_symbol(tradingsymbol)
        resp = xm.get_option_symbol(
            exchangeSegment=2,
            series=parsed["series"],
            symbol=parsed["symbol"],
            expiryDate=parsed["expiry_api_format"],
            optionType=parsed["option_type"],
            strikePrice=parsed["strike"],
        )
        if resp and resp.get("type") == "success" and resp.get("result"):
            return resp["result"][0]
    except Exception:
        pass
    return None


def parse_zerodha_option_symbol(sym: str) -> Dict[str, Any]:
    i = 0
    while i < len(sym) and sym[i].isalpha():
        i += 1
    underlying = sym[:i]
    rest = sym[i:]
    yy = rest[:2]
    mon_code = rest[2]
    dd = rest[3:5]
    tail = rest[5:]
    opt_type = "PE" if tail.endswith("PE") else "CE"
    strike_str = tail[:-2]
    strike = int(strike_str)
    mon_map = {
        'J': 'Jan', 'F': 'Feb', 'M': 'Mar', 'A': 'Apr', 'Y': 'May', 'H': 'Jun',
        'G': 'Jul', 'U': 'Aug', 'S': 'Sep', 'O': 'Oct', 'N': 'Nov', 'D': 'Dec'
    }
    mon = mon_map[mon_code]
    yyyy = f"20{yy}"
    expiry_api_format = f"{dd}{mon}{yyyy}"
    idx_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
    series = "OPTIDX" if underlying in idx_symbols else "OPTSTK"
    return {
        "symbol": underlying,
        "expiry_api_format": expiry_api_format,
        "option_type": opt_type,
        "strike": strike,
        "series": series,
    }


def main() -> None:
    start_time = datetime.now(timezone.utc)
    mapping_path = Path(MAPPING_FILE)
    mapping = load_mapping(mapping_path)

    # Read creds
    z_creds = read_csv_kv(Path(Z_CRED_CSV))
    fp_creds = read_csv_kv(Path(FP_CRED_CSV))

    # Zerodha login
    api_key = z_creds.get("key") or z_creds.get("api_key")
    api_secret = z_creds.get("secret") or z_creds.get("api_secret")
    request_token = z_creds.get("request_token")
    user_id = z_creds.get("id") or z_creds.get("userid") or z_creds.get("zerodhauserid")
    password = z_creds.get("pwd") or z_creds.get("password") or z_creds.get("zerodhapassword")
    totp_secret = z_creds.get("zerodha2fa") or z_creds.get("2fa") or z_creds.get("totp")

    if request_token:
        kite, _ = z_login(api_key=api_key, api_secret=api_secret, request_token=request_token)
    else:
        kite, _ = z_login(api_key=api_key, api_secret=api_secret, user_id=user_id, password=password, totp_secret=totp_secret, headless=True)
    log_line("Successful login to Zerodha")

    # 5paisa login
    interactive_key = fp_creds.get("interactive_api_key")
    interactive_secret = fp_creds.get("interactive_api_secret")
    market_key = fp_creds.get("market_data_api_key")
    market_secret = fp_creds.get("market_data_api_secret_key")
    source = fp_creds.get("source") or "WEBAPI"

    xt_i = XTSConnect(apiKey=interactive_key, secretKey=interactive_secret, source=source)
    iresp = xt_i.interactive_login()
    if not iresp or iresp.get("type") != "success":
        log_line(f"5paisa interactive login failed: {iresp}")
        sys.exit(2)
    xm = XTSConnect(apiKey=market_key, secretKey=market_secret, source=source)
    mresp = xm.marketdata_login()
    if not mresp or mresp.get("type") != "success":
        log_line(f"5paisa marketdata login failed: {mresp}")
        sys.exit(3)
    log_line("Successful login to 5paisa (Interactive + MarketData)")

    multiplier = read_multiplier(z_creds)
    log_line(f"Copy trader started. Multiplier={multiplier}")

    seen = set(mapping.get("orders", {}).keys())

    log_line("Starting copy loop: polling Zerodha orders...")
    while True:
        try:
            orders = kite.orders()
        except Exception as e:
            log_line(f"Error fetching Zerodha orders: {e}")
            time.sleep(2)
            continue

        for o in orders:
            zid = str(o.get("order_id"))
            status = (o.get("status") or "").upper()
            ts = o.get("order_timestamp")
            exch = o.get("exchange")
            symbol = o.get("tradingsymbol")
            qty = int(o.get("filled_quantity") or o.get("quantity") or 0)
            side = (o.get("transaction_type") or "").upper()

            # Skip before start
            try:
                if ts:
                    # Zerodha returns "%Y-%m-%d %H:%M:%S" in local time; treat as naive and skip compare strictly by start
                    pass
            except Exception:
                pass

            if status == "COMPLETE" and ts:
                # Skip pre-start orders once
                # We log pre-start orders once and do not copy
                # Parse naive -> treat as local, compare string to avoid tz mismatch
                # We will enforce cutoff by remembering first loop snapshot: only copy orders not seen and not older than now
                pass

            if zid in seen:
                continue

            if status != "COMPLETE":
                continue

            # Cutoff: only copy orders first seen after start_time by runtime snapshot approach
            # If order creation looks older (no reliable tz), rely on mapping absence and runtime start boundary:
            # We add everything we see from now on; for first loop, mark older ones as skipped

            # First-run skip logging for older orders
            # A simple heuristic: if mapping is empty and we are in the first few seconds, skip once
            # For clarity to user, log skip message if file was empty at start
            if mapping.get("_started") is None:
                mapping["_started"] = datetime.now().isoformat()
                save_mapping(mapping_path, mapping)
                # Mark all existing orders as seen without copying, with log
                log_line(f"Fetched {len(orders)} existing orders before copier start. These will NOT be tracked.")
                for ho in orders:
                    hid = str(ho.get("order_id"))
                    if hid not in seen:
                        mapping["orders"][hid] = {"skipped": True, "reason": "opened before start"}
                        seen.add(hid)
                        log_line(f"Pre-start order not tracked: Z {hid} symbol={ho.get('tradingsymbol')} status={ho.get('status')}")
                save_mapping(mapping_path, mapping)
                # After initial snapshot, continue to next poll iteration
                time.sleep(2)
                break

            # Resolve instrument on 5paisa
            inst = resolve_5p_instrument(xm, exch, symbol)
            if not inst:
                log_line(f"Resolve failed for {symbol} ({exch}); skipping Z {zid}")
                seen.add(zid)
                continue

            exchange_segment = inst.get("ExchangeSegment") or 2
            exchange_instrument_id = inst.get("ExchangeInstrumentID")
            target_qty = max(1, int(qty) * multiplier)
            order_side = "BUY" if side == "BUY" else "SELL"
            product_type = 2  # NRML for NSEFO per 5paisa enums
            time_in_force = "DAY"

            # Place order on 5paisa
            params = {
                "exchangeSegment": exchange_segment,
                "exchangeInstrumentID": exchange_instrument_id,
                "productType": "NRML",
                "orderType": "MARKET",
                "orderSide": order_side,
                "timeInForce": time_in_force,
                "disclosedQuantity": 0,
                "orderQuantity": target_qty,
                "limitPrice": 0,
                "stopPrice": 0,
                "orderUniqueIdentifier": f"Z2F-{zid}",
                "apiOrderSource": "WEBAPI",
            }

            log_line(f"Z COMPLETE {zid} {symbol} {side} zqty={qty} -> 5p qty={target_qty} inst={exchange_instrument_id}")
            try:
                presp = xt_i.place_order(**params)
                if presp and presp.get("type") == "success":
                    fivep_id = presp.get("result", {}).get("AppOrderID") or presp.get("result", {}).get("appOrderID")
                    mapping["orders"][zid] = {"fivep": fivep_id, "symbol": symbol, "side": side, "qty": qty, "tqty": target_qty}
                    save_mapping(mapping_path, mapping)
                    seen.add(zid)
                    log_line(f"Mapped Z {zid} -> 5p {fivep_id}")
                else:
                    log_line(f"5p order failed for Z {zid}: {presp}")
                    seen.add(zid)
            except Exception as e:
                log_line(f"5p place order exception for Z {zid}: {e}")
                seen.add(zid)

        time.sleep(2)


if __name__ == "__main__":
    main()


