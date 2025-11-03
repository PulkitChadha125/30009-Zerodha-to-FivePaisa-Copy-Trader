from __future__ import annotations

from typing import Dict, List, Tuple, Optional

import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from kiteconnect import KiteConnect
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import pyotp


def login(
    api_key: str,
    api_secret: str,
    request_token: Optional[str] = None,
    user_id: Optional[str] = None,
    password: Optional[str] = None,
    totp_secret: Optional[str] = None,
    chromedriver_path: Optional[str] = None,
    headless: bool = True,
) -> Tuple[KiteConnect, str]:
    """
    Complete the Zerodha login by exchanging the request token for an access token.

    Returns a tuple of (KiteConnect client, access_token).

    Usage flow (outside this function):
      1) Direct user to `kite.login_url()` to obtain a request_token via redirect
      2) Call this function with the `request_token`

    Raises an Exception with the underlying SDK error message if the exchange fails.
    """
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required")

    kite = KiteConnect(api_key=api_key)

    # If a request_token is already available, use it directly
    if request_token:
        try:
            session_data: Dict[str, str] = kite.generate_session(request_token, api_secret=api_secret)
            access_token: str = session_data["access_token"]
            kite.set_access_token(access_token)
            return kite, access_token
        except Exception as exc:
            raise Exception(f"Zerodha login failed: {exc}") from exc

    # Otherwise, attempt auto-login via Selenium using credentials and TOTP
    if not (user_id and password and totp_secret):
        raise ValueError(
            "request_token not provided. To auto-login, provide user_id, password, and totp_secret."
        )

    # Setup headless Chrome (prefer Selenium Manager if no path provided)
    try:
        options = Options()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # Create driver and open login page
        if chromedriver_path:
            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=options)
        else:
            # Use Selenium Manager to auto-download/manage the correct driver
            driver = webdriver.Chrome(options=options)
        try:
            driver.get(kite.login_url())
            driver.implicitly_wait(10)

            # Enter user id
            username_el = driver.find_element(By.XPATH, '//*[@id="userid"]')
            username_el.send_keys(user_id)

            # Enter password
            password_el = driver.find_element(By.XPATH, '//*[@id="password"]')
            password_el.send_keys(password)

            # Click login button
            login_btn = driver.find_element(By.XPATH, '//*[@id="container"]/div/div/div[2]/form/div[4]/button')
            login_btn.click()

            # Wait and enter TOTP PIN
            time.sleep(6)
            pin_el = driver.find_element(By.XPATH, '//*[@id="container"]/div[2]/div/div[2]/form/div[1]/input')
            totp = pyotp.TOTP(totp_secret)
            token = totp.now()
            pin_el.send_keys(token)

            # Give time for redirect
            time.sleep(6)
            url = driver.current_url
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            req_token = (query_params.get("request_token") or [None])[0]
            if not req_token:
                raise Exception("Failed to obtain request_token from redirected URL")

            # Save request_token
            Path("request_token.txt").write_text(req_token, encoding="utf-8")

        finally:
            try:
                driver.quit()
            except Exception:
                pass

        # Exchange request_token for access_token
        try:
            session_data: Dict[str, str] = kite.generate_session(req_token, api_secret=api_secret)
            access_token: str = session_data["access_token"]
            kite.set_access_token(access_token)

            # Persist access token
            Path("access_token.txt").write_text(access_token, encoding="utf-8")

            return kite, access_token
        except Exception as exc:
            raise Exception(f"Zerodha login (session exchange) failed: {exc}") from exc
    finally:
        pass


def fetch_completed_orders(kite: KiteConnect) -> List[Dict]:
    """
    Fetch and return all orders with status marked as completed.

    The Zerodha API uses status value 'COMPLETE' for fully executed orders.
    Returns a list of order dictionaries as provided by the SDK.
    """
    if kite is None:
        raise ValueError("kite client is required")

    try:
        all_orders: List[Dict] = kite.orders()
    except Exception as exc:
        raise Exception(f"Failed to fetch orders: {exc}") from exc

    completed = [order for order in all_orders if str(order.get("status", "")).upper() == "COMPLETE"]
    return completed


