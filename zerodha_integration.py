from __future__ import annotations

from typing import Dict, List, Tuple, Optional

import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from kiteconnect import KiteConnect
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
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
            print("[Zerodha] Using existing request_token. Exchanging for access_token in 2s...")
            time.sleep(2)
            session_data: Dict[str, str] = kite.generate_session(request_token, api_secret=api_secret)
            access_token: str = session_data["access_token"]
            kite.set_access_token(access_token)
            print("[Zerodha] Access token set. Proceeding in 2s...")
            time.sleep(2)
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
            print("[Zerodha] Opening login page. Waiting 2s...")
            driver.get(kite.login_url())
            time.sleep(2)
            wait = WebDriverWait(driver, 30)

            # Enter user id
            try:
                username_el = wait.until(EC.presence_of_element_located((By.ID, 'userid')))
            except Exception:
                username_el = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="userid"]')))
            username_el.send_keys(user_id)
            print("[Zerodha] Entered user ID. Waiting 2s before entering password...")
            time.sleep(2)

            # Enter password
            try:
                password_el = driver.find_element(By.ID, 'password')
            except Exception:
                password_el = driver.find_element(By.XPATH, '//*[@id="password"]')
            password_el.send_keys(password)
            print("[Zerodha] Entered password. Waiting 2s before clicking login...")
            time.sleep(2)

            # Click login button
            try:
                login_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            except Exception:
                login_btn = driver.find_element(By.XPATH, '//*[@id="container"]/div/div/div[2]/form/div[4]/button')
            login_btn.click()
            print("[Zerodha] Clicked login. Waiting 2s for 2FA screen...")
            time.sleep(2)

            # Wait and enter TOTP/PIN - target numeric 6-digit field; avoid selecting the password field
            pin_el = None
            last_err = None
            try:
                # Most reliable: 6-digit numeric field
                pin_el = WebDriverWait(driver, 20).until(
                    EC.visibility_of_element_located((By.XPATH, "//input[@type='number' and @maxlength='6']"))
                )
            except Exception as e:
                last_err = e
                # exhaustive fallbacks (explicit 2FA container path first)
                pin_locators = [
                    (By.XPATH, '//*[@id="container"]/div[2]/div/div[2]/form/div[1]/input'),
                    (By.XPATH, '/html/body/div[1]/div/div[2]/div[1]/div[2]/div/div[2]/form/div[1]/input'),
                    (By.ID, 'pin'),
                    (By.NAME, 'pin'),
                    (By.CSS_SELECTOR, 'input#pin'),
                    (By.CSS_SELECTOR, "input[placeholder='••••••']"),
                ]
                for by, sel in pin_locators:
                    try:
                        candidate = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((by, sel)))
                        # Avoid password field
                        cid = (candidate.get_attribute('id') or '').lower()
                        cname = (candidate.get_attribute('name') or '').lower()
                        itype = (candidate.get_attribute('type') or '').lower()
                        if cid == 'password' or cname == 'password':
                            continue
                        pin_el = candidate
                        if pin_el:
                            break
                    except Exception as e2:
                        last_err = e2
                        continue
            if pin_el is None:
                try:
                    driver.save_screenshot("zerodha_login_no_pin.png")
                    Path("zerodha_login_no_pin.html").write_text(driver.page_source or "", encoding="utf-8")
                except Exception:
                    pass
                raise Exception(f"Unable to locate TOTP/PIN field. Last error: {last_err}")
            # Some UIs have 1 input; others split into 6 boxes. Handle both.
            totp = pyotp.TOTP(totp_secret)
            token = totp.now()
            print("[Zerodha] Ready to enter TOTP. Waiting 2s so you can observe...")
            time.sleep(2)
            try:
                # Try multiple inputs first
                # Focus the element first (helps some numeric inputs)
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", pin_el)
                    pin_el.click()
                except Exception:
                    pass

                otp_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
                otp_inputs = [el for el in otp_inputs if el.is_displayed() and el.is_enabled()]
                if len(otp_inputs) >= 4 and len(token) >= 4:
                    for i, ch in enumerate(token[:len(otp_inputs)]):
                        otp_inputs[i].clear()
                        otp_inputs[i].send_keys(ch)
                    # Press Enter on last box
                    otp_inputs[min(len(otp_inputs)-1, len(token)-1)].send_keys(Keys.ENTER)
                else:
                    try:
                        pin_el.clear()
                    except Exception:
                        pass
                    pin_el.send_keys(token)
                    pin_el.send_keys(Keys.ENTER)
            except Exception:
                try:
                    pin_el.clear()
                except Exception:
                    pass
                pin_el.send_keys(token)
                pin_el.send_keys(Keys.ENTER)
            print("[Zerodha] Entered TOTP. Waiting 2s before continuing...")
            time.sleep(2)

            # If there's a submit/continue button after PIN, click it
            cont_locators = [
                (By.XPATH, '//*[@id="container"]/div[2]/div/div[2]/form/div[2]/button'),  # explicit continue
                (By.CSS_SELECTOR, 'button[type="submit"]'),
                (By.XPATH, '//*[@id="container"]/div[2]/div/div[2]/form//button'),
                (By.XPATH, '//form//button[@type="submit"]'),
            ]
            for by, sel in cont_locators:
                try:
                    cont_btn = driver.find_element(by, sel)
                    cont_btn.click()
                    break
                except Exception:
                    continue
            print("[Zerodha] Clicked continue. Waiting 2s for redirect...")
            time.sleep(2)

            # Wait for redirect URL containing request_token (retry once if needed)
            try:
                wait.until(lambda d: "request_token=" in d.current_url)
            except Exception:
                # Retry once with a fresh TOTP in case the first expired
                try:
                    pin_el.clear()
                except Exception:
                    pass
                # Re-locate pin field if needed (prefer numeric 6-digit field; avoid password)
                try:
                    pin_el = WebDriverWait(driver, 10).until(
                        EC.visibility_of_element_located((By.XPATH, "//input[@type='number' and @maxlength='6']"))
                    )
                except Exception:
                    try:
                        pin_el = WebDriverWait(driver, 10).until(
                            EC.visibility_of_element_located((By.XPATH, '//*[@id="container"]/div[2]/div/div[2]/form/div[1]/input'))
                        )
                    except Exception:
                        try:
                            pin_el = driver.find_element(By.ID, 'pin')
                        except Exception:
                            try:
                                pin_el = driver.find_element(By.CSS_SELECTOR, "input[placeholder='••••••']")
                            except Exception:
                                pin_el = driver.find_element(By.XPATH, "//input[@type='password']")
                token = pyotp.TOTP(totp_secret).now()
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", pin_el)
                    pin_el.click()
                except Exception:
                    pass
                pin_el.send_keys(token)
                for by, sel in cont_locators:
                    try:
                        cont_btn = driver.find_element(by, sel)
                        cont_btn.click()
                        break
                    except Exception:
                        continue
                wait.until(lambda d: "request_token=" in d.current_url)
                print("[Zerodha] Retried TOTP. Waiting 2s for redirect...")
                time.sleep(2)

            url = driver.current_url
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            req_token = (query_params.get("request_token") or [None])[0]
            if not req_token:
                # Persist debug artifacts for diagnosis
                try:
                    driver.save_screenshot("zerodha_login_debug.png")
                    Path("zerodha_login_debug.html").write_text(driver.page_source or "", encoding="utf-8")
                except Exception:
                    pass
                raise Exception("Failed to obtain request_token from redirected URL")

            # Save request_token
            Path("request_token.txt").write_text(req_token, encoding="utf-8")
            print("[Zerodha] Captured request_token. Waiting 2s before closing browser...")
            time.sleep(2)

        finally:
            try:
                driver.quit()
            except Exception:
                pass

        # Exchange request_token for access_token
        try:
            print("[Zerodha] Exchanging request_token for access_token in 2s...")
            time.sleep(2)
            session_data: Dict[str, str] = kite.generate_session(req_token, api_secret=api_secret)
            access_token: str = session_data["access_token"]
            kite.set_access_token(access_token)

            # Persist access token
            Path("access_token.txt").write_text(access_token, encoding="utf-8")
            print("[Zerodha] Access token saved. Waiting 2s before returning...")
            time.sleep(2)

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


