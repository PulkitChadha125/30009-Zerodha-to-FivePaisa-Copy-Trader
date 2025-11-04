# Zerodha → 5paisa Copy Trader

This project logs into Zerodha using the official PyKiteConnect SDK, reads completed orders, and mirrors them to 5paisa (XTS) with a quantity multiplier, while avoiding duplicates and writing human-readable logs.

## Contents
- zerodha_integration.py: Zerodha login (session exchange or Selenium auto-login with TOTP) and utility helpers
- main.py: Quick login + dump Zerodha orders for inspection and logging
- FivePaisa.py: 5paisa login and symbol resolver demo (get_option_symbol)
- copy_trader.py: Copy-trader service that mirrors Zerodha completed orders to 5paisa
- Orderlog.txt: Append-only text log of actions/events

## Prerequisites
- Python 3.10+
- Google Chrome installed (for Selenium auto-login)
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

## Credentials
- Zerodha: `ZerodhaCredentials.csv` (two columns: title,value). Supported keys:
  - key, secret
  - id / userid / ZerodhaUserId
  - pwd / password / ZerodhaPassword
  - Zerodha2fa (Base32 TOTP secret)
  - request_token (optional; skips browser login if present)
  - chromedriver (optional path; Selenium Manager used otherwise)
  - CopyTradeQtyMultiplier (optional; integer multiplier for 5p qty)
- 5paisa: `FivePaisaCredentials.csv` (two columns: title,value)
  - interactive_api_key, interactive_api_secret
  - market_data_api_key, market_data_api_secret_key
  - source (default WEBAPI)

## Zerodha Login Flow
Two supported flows, both via the official SDK:
- Request-token exchange: if `request_token` is present, we directly call `kite.generate_session()` to get `access_token`.
- Auto-login (Selenium): when `request_token` isn’t present, `zerodha_integration.login()` opens the Zerodha login page, fills user/password and TOTP (generated via pyotp), and clicks Continue. It extracts `request_token` from the final redirect URL and exchanges it for `access_token`.

Reference SDK: `https://github.com/zerodha/pykiteconnect`

## 5paisa Login
We use the XTS Python client bundled in `XTS/`:
- Interactive login for order placement
- Market Data login for symbol resolution (option and future instruments)

## Instrument Resolution (Zerodha → 5paisa)
For index options like `NIFTY25N0425800PE`, we parse Zerodha symbol into:
- series: `OPTIDX` (or `OPTSTK` for stock options)
- symbol: `NIFTY`
- expiryDate: `ddMonYYYY` (e.g., `04Nov2025`)
- optionType: `CE` or `PE`
- strikePrice: integer (e.g., `25800`)

We then call 5paisa `get_option_symbol(exchangeSegment=2, ...)` to retrieve `ExchangeInstrumentID` for order placement. Similar flow can be added for futures via `get_future_symbol`.

## Copy-Trader Approach (copy_trader.py)
- Start cutoff: on first run, we mark all currently visible Zerodha orders as historical and do not copy them. We log: “opened before start; not tracked”. New orders seen after start are eligible for copying.
- Source of truth: Zerodha `orders()` filtered for `status=COMPLETE` as the atomic trading events to mirror.
- De-duplication: we persist a mapping in `copy_map.json` of `zerodha_order_id → fivepaisa_order_id` and skip already mapped orders.
- Quantity multiplier: 5paisa order quantity = Zerodha filled quantity × `CopyTradeQtyMultiplier` (read from Zerodha CSV; defaults to 1; clamped to ≥1).
- Order placement: 5paisa MARKET order with `productType=NRML`, `timeInForce=DAY`. Side mirrors Zerodha (`BUY`/`SELL`).
- Logging: every step appended to `Orderlog.txt` with timestamps, including resolve failures and mapping results.
- Poll interval: ~2 seconds.

## Running
- Inspect Zerodha orders quickly (and log them):
  ```bash
  python main.py
  ```
- Test 5paisa symbol resolution (example option):
  ```bash
  python FivePaisa.py
  ```
- Start the copy trader service:
  ```bash
  python copy_trader.py
  ```

## Logs
- `Orderlog.txt`: Append-only logs such as:
  - Z-ORDER lines from `main.py`
  - Copy-trader events: historical skip, resolve success/fail, placed 5p order with qty, mapping `Z → 5p` ids.

## Notes & Limitations
- Headless browser login: set `headless=True` (default) for Selenium; temporarily set to False in `main.py` if you need to see the page.
- Redirect URL in Zerodha dev console must match exactly. Any HTTPS URL is fine; the script reads the final URL to extract `request_token`.
- TOTP must be generated from a valid Base32 secret; clock skew can cause failures.
- This MVP resolves only index options out-of-the-box; extend `copy_trader.py` resolver for stock options/futures as needed.
- Product/type mappings can be customized per your risk/rules.

## Roadmap (suggested)
- Add futures resolution (`get_future_symbol`) and stock options (`OPTSTK`)
- Enrich product type mapping (CNC/MIS/NRML → 5paisa enums) per exchange
- WebSocket streaming to reduce poll latency
- Robust persistence (SQLite) for mappings and audit logs


