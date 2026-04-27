from playwright.sync_api import sync_playwright
import os
import time
from logger import log
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def auto_login(browser=None):
    """
    Automatically logs into the website using credentials from .env and saves the session state.
    If a browser instance is provided, it uses it; otherwise, it launches a new one.
    """
    username = os.getenv("WEB_USERNAME")
    password = os.getenv("WEB_PASSWORD")
    
    if not username or not password:
        log.error("❌ WEB_USERNAME သို့မဟုတ် WEB_PASSWORD ကို .env ထဲမှာ မတွေ့ပါ။")
        return False, "Credentials missing in .env"

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(base_dir, "state.json")
    login_url = "https://www.carrymanexpress.com/login"

    p = None
    should_close_browser = False
    
    try:
        if not browser:
            p = sync_playwright().start()
            browser = p.chromium.launch(headless=True)
            should_close_browser = True
            
        log.info("🌐 Automatic Login စတင်နေပါပြီ...")
        context = browser.new_context()
        page = context.new_page()

        log.info(f"🔗 {login_url} သို့ သွားနေပါသည်...")
        page.goto(login_url)
        page.wait_for_load_state('networkidle')

        log.info("🔑 Login အချက်အလက်များ ရိုက်ထည့်နေပါသည်...")
        # Using XPATHs from existing test_login.py
        page.fill("//input[@type='text']", username)
        page.fill("//input[@type='password']", password)

        log.info("🖱️ Login ခလုတ်ကို နှိပ်လိုက်ပါပြီ။")
        page.click("//button[.//span[contains(translate(text(), 'LOGIN', 'login'), 'login')]]")

        # Wait for navigation or a specific element that indicates successful login
        log.info("⏳ Login အောင်မြင်သည်အထိ စောင့်နေပါသည်...")
        time.sleep(5) # Give it some time to process and redirect
        page.wait_for_load_state('networkidle')

        # Check if we are still on the login page
        if "login" in page.url.lower():
            log.error("❌ Login မအောင်မြင်ပါ။ Username သို့မဟုတ် Password မှားယွင်းနေနိုင်ပါသည်။")
            # Debug screenshot
            error_path = os.path.join(base_dir, "login_error.png")
            page.screenshot(path=error_path)
            log.info(f"📸 Login အမှားကို စစ်ဆေးနိုင်ရန် '{error_path}' မှာ Screenshot ရိုက်ထားပါတယ်")
            return False, "Login failed: Still on login page"

        # Save the storage state
        log.info(f"💾 Session state ကို {state_path} မှာ Update လုပ်နေပါသည်...")
        context.storage_state(path=state_path)
        log.info("✅ Session state ကို အောင်မြင်စွာ Update လုပ်ပြီးပါပြီ။")
        
        context.close()
        return True, "Login successful"

    except Exception as e:
        log.error(f"❌ Auto Login Error: {str(e)}")
        return False, str(e)
    finally:
        if should_close_browser and browser:
            browser.close()
        if p:
            p.stop()

if __name__ == "__main__":
    # Direct testing
    success, msg = auto_login()
    if success:
        print("🎉 Auto Login Success!")
    else:
        print(f"❌ Auto Login Failed: {msg}")
