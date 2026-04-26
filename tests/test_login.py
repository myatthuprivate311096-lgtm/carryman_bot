from playwright.sync_api import sync_playwright
import time
import os

def run_test_login():
    with sync_playwright() as p:
        print("🌐 Browser စတင် ဖွင့်နေပါပြီ...")
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context()
        page = context.new_page()

        # ၁။ Login ဝင်ရမည့် Website လင့်ခ်
        login_url = "https://www.carrymanexpress.com/login"
        print(f"🔗 {login_url} သို့ သွားနေပါသည်...")
        page.goto(login_url)
        page.wait_for_load_state('networkidle')

        # ၂။ Username နှင့် Password ရိုက်ထည့်မည် (အစ်ကို့ Selenium XPATH များအတိုင်း)
        print("🔑 Login အချက်အလက်များ ရိုက်ထည့်နေပါသည်...")
        page.fill("//input[@type='text']", "test_account")    # Username
        page.fill("//input[@type='password']", "12345678")    # Password

        # ၃။ Login ခလုတ်ကို နှိပ်မည်
        page.click("//button[.//span[contains(translate(text(), 'LOGIN', 'login'), 'login')]]")

        # ၄။ Website အထဲရောက်သွားသည်အထိ ခဏစောင့်မည်
        print("⏳ အထဲရောက်သည်အထိ စောင့်နေပါသည်...")
        time.sleep(3) # UI ပြောင်းသွားချိန် ခဏစောင့်ရန်
        page.wait_for_load_state('networkidle')
        
        # ၅။ နောက်တစ်ခါ Login ထပ်ဝင်စရာမလိုအောင် Session ကို သိမ်းမည်
        # Root directory ထဲမှာ သိမ်းရန် path ပြင်ဆင်ခြင်း
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        state_path = os.path.join(base_dir, "state.json")
        
        context.storage_state(path=state_path)
        print(f"✅ Session ကို '{state_path}' တွင် သိမ်းဆည်းပြီးပါပြီ။")

        browser.close()

if __name__ == "__main__":
    run_test_login()
