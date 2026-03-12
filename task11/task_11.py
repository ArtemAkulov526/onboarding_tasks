from playwright.sync_api import sync_playwright
from curl_cffi import requests
from dotenv import load_dotenv
import os
import psutil
import re

load_dotenv()

email = os.getenv("DATABASUS_LOGIN", default="login")
password = os.getenv("DATABASUS_PASSWORD", default="password")

def get_nst_debug_port():
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if proc.info['name'] and 'nstchrome.exe' in proc.info['name'].lower():
                cmdline = " ".join(proc.info['cmdline'])
                match = re.search(r'--remote-debugging-port=(\d+)', cmdline)
                if match:
                    return int(match.group(1))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


class Task11:

    def login(self):
        url = "https://app.databasus.com"
        with sync_playwright() as p:
            port = get_nst_debug_port()
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")

            context = browser.contexts[0]
            page = context.new_page()

            page.goto(url)

            # NEEDED FOR COOKIE
            # first time visit need to collect cookie by clicking on understood
            # page.wait_for_timeout(31000)
            understood = page.get_by_role("button", name="Understood")
            if understood.is_visible():
                understood.click()

            page.get_by_role("button", name="Sign in").click()

            page.locator('input[type="email"]').fill(email)
            page.locator('input[type="password"]').fill(password)

            # to log into account
            page.wait_for_timeout(5000)
            with page.expect_response(lambda r: "signin" in r.url) as response_info:
                page.get_by_role("button", name="Sign in").click()

            response = response_info.value
            data = response.json()

            token = data["token"]
            page.close()
            return token

    def make_request(self, token):
        headers = {"Authorization": f"{token}",
                   "Content-Type": "application/json",
                   "Accept": "application/json"}
        url = "https://app.databasus.com/api/v1/workspaces"
        resp = requests.get(url=url, headers=headers, impersonate="chrome131")
        resp.raise_for_status()
        print(f"API status code:",resp.status_code)
        print(f"API response",resp.json())

if __name__ == "__main__":
    task = Task11()
    token = task.login()
    task.make_request(token)