import csv
import json
import math
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
        log_line(f"Parsed {tradingsymbol}: symbol={parsed['symbol']}, expiry={parsed['expiry_api_format']}, strike={parsed['strike']}, type={parsed['option_type']}, is_monthly={parsed.get('is_monthly_expiry', False)}")
        
        # Determine exchange segment for get_option_symbol (numeric)
        # For SENSEX use 12 (BSEFO), for others use 2 (NSEFO)
        # Use parsed symbol (e.g., "SENSEX") instead of full tradingsymbol for more reliable detection
        exchange_segment = get_exchange_segment_numeric_for_marketdata(parsed["symbol"])
        log_line(f"Using exchangeSegment={exchange_segment} for {tradingsymbol} (symbol={parsed['symbol']})")
        
        # If monthly expiry, fetch actual expiry date from API
        expiry_date = parsed["expiry_api_format"]
        if parsed.get("is_monthly_expiry", False):
            # For monthly expiry, get list of expiry dates and find the one matching the month/year
            try:
                expiry_resp = xm.get_expiry_date(
                    exchangeSegment=exchange_segment,
                    series=parsed["series"],
                    symbol=parsed["symbol"]
                )
                # get_expiry_date returns result directly (not wrapped in type:success)
                if expiry_resp and expiry_resp.get("result"):
                    expiry_dates = expiry_resp["result"]
                    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                    
                    # Extract month and year from parsed expiry (e.g., "01Nov2025" -> Nov 2025)
                    parsed_dt = datetime.strptime(parsed["expiry_api_format"], "%d%b%Y")
                    target_month = parsed_dt.month
                    target_year = parsed_dt.year
                    
                    # Filter expiry dates to match the month and year from symbol
                    matching_dates = []
                    for exp_date_str in expiry_dates:
                        try:
                            dt = datetime.strptime(exp_date_str, "%Y-%m-%dT%H:%M:%S")
                            if dt.month == target_month and dt.year == target_year:
                                matching_dates.append(exp_date_str)
                        except Exception:
                            continue
                    
                    if matching_dates:
                        # Sort matching dates (latest first) and take the latest one
                        matching_dates_sorted = sorted(matching_dates, reverse=True)
                        latest_expiry = matching_dates_sorted[0]
                        # Convert from "2025-11-25T14:30:00" to "25Nov2025" format
                        dt = datetime.strptime(latest_expiry, "%Y-%m-%dT%H:%M:%S")
                        expiry_date = f"{dt.day:02d}{month_names[dt.month-1]}{dt.year}"
                        log_line(f"Monthly expiry: Using expiry date {expiry_date} for {parsed['symbol']} (matched from {len(matching_dates)} dates in {month_names[target_month-1]} {target_year})")
                    else:
                        log_line(f"No matching expiry date found for {parsed['symbol']} in {month_names[target_month-1]} {target_year} from {len(expiry_dates)} available dates")
                elif expiry_resp and expiry_resp.get("type") == "error":
                    log_line(f"get_expiry_date error for {parsed['symbol']}: {expiry_resp.get('description', expiry_resp)}")
                else:
                    log_line(f"get_expiry_date unexpected response for {parsed['symbol']}: {expiry_resp}")
            except Exception as e:
                log_line(f"Error fetching expiry date for monthly expiry {tradingsymbol}: {e}")
                # Fall back to parsed expiry_date
        
        resp = xm.get_option_symbol(
            exchangeSegment=exchange_segment,
            series=parsed["series"],
            symbol=parsed["symbol"],
            expiryDate=expiry_date,
            optionType=parsed["option_type"],
            strikePrice=parsed["strike"],
        )
        if resp and resp.get("type") == "success" and resp.get("result"):
            return resp["result"][0]
        else:
            log_line(f"get_option_symbol failed for {tradingsymbol}: {resp}")
    except Exception as e:
        log_line(f"Error resolving {tradingsymbol}: {e}")
    return None


def parse_zerodha_option_symbol(sym: str) -> Dict[str, Any]:
    i = 0
    while i < len(sym) and sym[i].isalpha():
        i += 1
    underlying = sym[:i]
    rest = sym[i:]
    
    # Month name mapping (3-char to full name) - for MONTHLY expiry
    month_name_map = {
        'JAN': 'Jan', 'FEB': 'Feb', 'MAR': 'Mar', 'APR': 'Apr', 'MAY': 'May', 'JUN': 'Jun',
        'JUL': 'Jul', 'AUG': 'Aug', 'SEP': 'Sep', 'OCT': 'Oct', 'NOV': 'Nov', 'DEC': 'Dec'
    }
    # Single char month code mapping - for WEEKLY expiry
    mon_map = {
        'J': 'Jan', 'F': 'Feb', 'M': 'Mar', 'A': 'Apr', 'Y': 'May', 'H': 'Jun',
        'G': 'Jul', 'U': 'Aug', 'S': 'Sep', 'O': 'Oct', 'N': 'Nov', 'D': 'Dec'
    }
    
    yy = rest[:2]
    yyyy = f"20{yy}"
    
    # Check if month is 3-char format (MONTHLY) or single char (WEEKLY)
    month_part = rest[2:5].upper()  # Try 3-char first (MONTHLY format)
    is_monthly_expiry = month_part in month_name_map
    
    if is_monthly_expiry:
        # MONTHLY format: BANKNIFTY25NOV59500CE
        # Format: SYMBOL + YY + MONTHNAME + STRIKE + OPTTYPE
        # No day in monthly expiry symbols
        mon = month_name_map[month_part]
        # For monthly expiry, use "01" as default day (will be replaced by API call)
        dd = "01"
        tail = rest[5:]  # Strike starts immediately after 3-char month
    else:
        # WEEKLY format: NIFTY25N1125550CE
        # Format: SYMBOL + YY + M + DD + STRIKE + OPTTYPE
        # 25 = year, N = month, 11 = day
        mon_code = rest[2]
        mon = mon_map[mon_code]
        dd = rest[3:5]  # Day is 2 digits after single char month
        tail = rest[5:]  # Strike starts after day
    
    opt_type = "PE" if tail.endswith("PE") else "CE"
    strike_str = tail[:-2]
    strike = int(strike_str)
    
    expiry_api_format = f"{dd}{mon}{yyyy}"
    idx_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
    series = "OPTIDX" if underlying in idx_symbols else "OPTSTK"
    
    return {
        "symbol": underlying,
        "expiry_api_format": expiry_api_format,
        "option_type": opt_type,
        "strike": strike,
        "series": series,
        "is_monthly_expiry": is_monthly_expiry,  # Flag to indicate if we need to fetch actual expiry date
    }


def get_exchange_segment_numeric_for_marketdata(symbol: str) -> int:
    """Determine exchange segment (numeric) for market data API calls.
    Returns 2 for NSEFO (NIFTY, BANKNIFTY, MIDCPNIFTY, FINNIFTY)
    Returns 12 for BSEFO (SENSEX)
    """
    symbol_upper = symbol.upper()
    if "SENSEX" in symbol_upper:
        return 12  # BSEFO
    # Default to NSEFO for NIFTY, BANKNIFTY, MIDCPNIFTY, FINNIFTY
    return 2  # NSEFO


def get_exchange_segment_string_for_order(symbol: str) -> str:
    """Determine exchange segment (string) for order placement.
    Returns "NSEFO" for NIFTY, BANKNIFTY, MIDCPNIFTY, FINNIFTY
    Returns "BSEFO" for SENSEX
    """
    symbol_upper = symbol.upper()
    # Check if symbol contains NIFTY, BANKNIFTY, MIDCPNIFTY, or FINNIFTY
    if any(x in symbol_upper for x in ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "FINNIFTY"]):
        return XTSConnect.EXCHANGE_NSEFO  # "NSEFO"
    elif "SENSEX" in symbol_upper:
        return XTSConnect.EXCHANGE_BSEFO  # "BSEFO"
    # Default to NSEFO
    return XTSConnect.EXCHANGE_NSEFO  # "NSEFO"


def get_ask(xm: XTSConnect, nfo_ins_id: int, symbol: str) -> Optional[float]:
    """Get best ask price for an instrument."""
    try:
        exchange_segment = get_exchange_segment_numeric_for_marketdata(symbol)
        response = xm.get_quote(
            Instruments=[{"exchangeSegment": exchange_segment, "exchangeInstrumentID": nfo_ins_id}],
            xtsMessageCode=1502,
            publishFormat='JSON'
        )
        
        if not response or response.get("type") != "success":
            log_line(f"Error getting ask price: {response}")
            return None
            
        list_quotes = response['result']['listQuotes'][0]
        quote_data = json.loads(list_quotes)
        
        # Best Ask = first entry in Asks (lowest price)
        if quote_data.get('Asks') and len(quote_data['Asks']) > 0:
            ask_price = quote_data['Asks'][0]['Price']
            return float(ask_price)
        return None
    except Exception as e:
        log_line(f"Error getting ask price: {e}")
        return None


def get_bid(xm: XTSConnect, nfo_ins_id: int, symbol: str) -> Optional[float]:
    """Get best bid price for an instrument."""
    try:
        exchange_segment = get_exchange_segment_numeric_for_marketdata(symbol)
        response = xm.get_quote(
            Instruments=[{"exchangeSegment": exchange_segment, "exchangeInstrumentID": nfo_ins_id}],
            xtsMessageCode=1502,
            publishFormat='JSON'
        )
        
        if not response or response.get("type") != "success":
            log_line(f"Error getting bid price: {response}")
            return None
            
        list_quotes = response['result']['listQuotes'][0]
        quote_data = json.loads(list_quotes)
        
        # Best Bid = first entry in Bids (highest price)
        if quote_data.get('Bids') and len(quote_data['Bids']) > 0:
            bid_price = quote_data['Bids'][0]['Price']
            return float(bid_price)
        return None
    except Exception as e:
        log_line(f"Error getting bid price: {e}")
        return None


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
        kite, _ = z_login(api_key=api_key, api_secret=api_secret, user_id=user_id, password=password, totp_secret=totp_secret, headless=False)
    log_line("Successful login to Zerodha")

    # 5paisa login
    log_line("Starting 5paisa login...")
    interactive_key = fp_creds.get("interactive_api_key")
    interactive_secret = fp_creds.get("interactive_api_secret")
    market_key = fp_creds.get("market_data_api_key")
    market_secret = fp_creds.get("market_data_api_secret_key")
    source = fp_creds.get("source") or "WEBAPI"

    log_line("Attempting 5paisa Interactive login...")
    xt_i = XTSConnect(apiKey=interactive_key, secretKey=interactive_secret, source=source)
    iresp = xt_i.interactive_login()
    log_line(f"5paisa Interactive login response: {json.dumps(iresp, indent=2)}")
    if not iresp or iresp.get("type") != "success":
        log_line(f"5paisa interactive login failed: {iresp}")
        sys.exit(2)
    log_line("5paisa Interactive login successful")
    
    log_line("Attempting 5paisa MarketData login...")
    xm = XTSConnect(apiKey=market_key, secretKey=market_secret, source=source)
    mresp = xm.marketdata_login()
    log_line(f"5paisa MarketData login response: {json.dumps(mresp, indent=2)}")
    if not mresp or mresp.get("type") != "success":
        log_line(f"5paisa marketdata login failed: {mresp}")
        sys.exit(3)
    log_line("5paisa MarketData login successful")
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

            if zid in seen:
                continue

            if status != "COMPLETE":
                continue

            # Check if order was created before application start
            # First-run: mark all existing orders as seen without copying
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

            # For subsequent runs, check if order timestamp is before start_time
            if ts:
                try:
                    # Handle both string and datetime object timestamps
                    if isinstance(ts, datetime):
                        # Already a datetime object, make it naive (remove timezone if present)
                        order_dt = ts.replace(tzinfo=None) if ts.tzinfo else ts
                    elif isinstance(ts, str):
                        # Parse string timestamp (format: "%Y-%m-%d %H:%M:%S" in local time, naive)
                        order_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    else:
                        # Unknown type, skip this check
                        order_dt = None
                    
                    if order_dt:
                        # Convert start_time (UTC) to local naive datetime for comparison
                        start_dt_local = start_time.astimezone().replace(tzinfo=None)
                        if order_dt < start_dt_local:
                            # Order was created before app start, skip it
                            mapping["orders"][zid] = {"skipped": True, "reason": "opened before start"}
                            seen.add(zid)
                            log_line(f"Pre-start order skipped: Z {zid} symbol={symbol} timestamp={ts}")
                            continue
                except Exception as e:
                    # If timestamp parsing fails, log and continue (better to skip than copy wrong order)
                    log_line(f"Error parsing order timestamp for Z {zid}: {e}")
                    seen.add(zid)
                    continue

            # Resolve instrument on 5paisa
            inst = resolve_5p_instrument(xm, exch, symbol)
            if not inst:
                log_line(f"Resolve failed for {symbol} ({exch}); skipping Z {zid}")
                seen.add(zid)
                continue

            # Determine exchange segment from symbol (string for order placement)
            exchange_segment = get_exchange_segment_string_for_order(symbol)
            exchange_instrument_id = inst.get("ExchangeInstrumentID")
            target_qty = max(1, int(qty) * multiplier)
            
            # Get price based on order side
            price = None
            if side == "BUY":
                price = get_ask(xm, exchange_instrument_id, symbol)
                if price is None:
                    log_line(f"Failed to get ask price for {symbol}; skipping Z {zid}")
                    seen.add(zid)
                    continue
            else:  # SELL
                price = get_bid(xm, exchange_instrument_id, symbol)
                if price is None:
                    log_line(f"Failed to get bid price for {symbol}; skipping Z {zid}")
                    seen.add(zid)
                    continue

            # Adjust price: add 1% for BUY, subtract 1% for SELL
            one_percent = price * 0.01
            adjusted_price = price
            ticksize = 0.05  # Default tick size for options (can be enhanced to fetch from instrument data)
            
            if side == "BUY":
                adjusted_price = price + one_percent
                # Round up to nearest multiple of ticksize for buy orders
                if ticksize and ticksize > 0:
                    adjusted_price = math.ceil(adjusted_price / ticksize) * ticksize
            else:  # SELL
                adjusted_price = price - one_percent
                # Round down to nearest multiple of ticksize for sell orders
                if ticksize and ticksize > 0:
                    adjusted_price = math.floor(adjusted_price / ticksize) * ticksize

            # Use XTS constants
            order_side_val = XTSConnect.TRANSACTION_TYPE_BUY if side == "BUY" else XTSConnect.TRANSACTION_TYPE_SELL

            # Place LIMIT order on 5paisa
            params = {
                "exchangeSegment": exchange_segment,
                "exchangeInstrumentID": exchange_instrument_id,
                "productType": XTSConnect.PRODUCT_MIS,
                "orderType": XTSConnect.ORDER_TYPE_LIMIT,
                "orderSide": order_side_val,
                "timeInForce": XTSConnect.VALIDITY_DAY,
                "disclosedQuantity": 0,
                "orderQuantity": target_qty,
                "limitPrice": adjusted_price,
                "stopPrice": 0,
                "orderUniqueIdentifier": f"Z2F-{zid}",
                "apiOrderSource": "WEBAPI",
            }

            log_line(f"Z COMPLETE {zid} {symbol} {side} zqty={qty} -> 5p qty={target_qty} inst={exchange_instrument_id} price={price:.2f} adjusted={adjusted_price:.2f}")
            try:
                print(f"\n[5paisa] Sending order parameters:")
                print(f"  exchangeSegment: {params['exchangeSegment']}")
                print(f"  exchangeInstrumentID: {params['exchangeInstrumentID']}")
                print(f"  productType: {params['productType']}")
                print(f"  orderType: {params['orderType']}")
                print(f"  orderSide: {params['orderSide']}")
                print(f"  timeInForce: {params['timeInForce']}")
                print(f"  disclosedQuantity: {params['disclosedQuantity']}")
                print(f"  orderQuantity: {params['orderQuantity']}")
                print(f"  limitPrice: {params['limitPrice']}")
                print(f"  stopPrice: {params['stopPrice']}")
                print(f"  orderUniqueIdentifier: {params['orderUniqueIdentifier']}")
                print(f"  apiOrderSource: {params['apiOrderSource']}")
                print(f"  Original price: {price:.2f}, Adjusted price: {adjusted_price:.2f}")
                print(f"  Full params dict: {params}\n")
                
                presp = xt_i.place_order(**params)
                
                print(f"[5paisa] Received response:")
                print(f"  Response type: {type(presp)}")
                print(f"  Full response: {presp}\n")
                
                if presp and presp.get("type") == "success":
                    fivep_id = presp.get("result", {}).get("AppOrderID") or presp.get("result", {}).get("appOrderID")
                    mapping["orders"][zid] = {"fivep": fivep_id, "symbol": symbol, "side": side, "qty": qty, "tqty": target_qty}
                    save_mapping(mapping_path, mapping)
                    seen.add(zid)
                    log_line(f"Mapped Z {zid} -> 5p {fivep_id}")
                else:
                    # Do NOT retry: mark seen and log status/description once
                    status = presp.get("code") if isinstance(presp, dict) else None
                    desc = presp.get("description") if isinstance(presp, dict) else str(presp)
                    log_line(f"5p order not completed for Z {zid} (no retry). status={status} desc={desc}")
                    seen.add(zid)
            except Exception as e:
                # Do NOT retry on exception either
                log_line(f"5p place order exception for Z {zid} (no retry): {e}")
                seen.add(zid)

        time.sleep(2)


if __name__ == "__main__":
    main()


