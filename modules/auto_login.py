import os
import asyncio
from logger import log
from dotenv import load_dotenv
from modules.browser_manager import browser_manager

# Load environment variables
load_dotenv()

async def _auto_login_task(page, **kwargs):
    """Async task for login logic"""
    username = os.getenv("WEB_USERNAME")
    password = os.getenv("WEB_PASSWORD")
    
    if not username or not password:
        log.error("❌ WEB_USERNAME သို့မဟုတ် WEB_PASSWORD ကို .env ထဲမှာ မတွေ့ပါ။")
        return False, "Credentials missing in .env"

    login_url = "https://www.carrymanexpress.com/login"
    log.info(f"🔗 {login_url} သို့ သွားနေပါသည်...")
    await page.goto(login_url)
    await page.wait_for_load_state('networkidle')

    log.info("🔑 Login အချက်အလက်များ ရိုက်ထည့်နေပါသည်...")
    await page.fill("//input[@type='text']", username)
    await page.fill("//input[@type='password']", password)

    log.info("🖱️ Login ခလုတ်ကို နှိပ်လိုက်ပါပြီ။")
    await page.click("//button[.//span[contains(translate(text(), 'LOGIN', 'login'), 'login')]]")

    log.info("⏳ Login အောင်မြင်သည်အထိ စောင့်နေပါသည်...")
    await asyncio.sleep(5)
    await page.wait_for_load_state('networkidle')

    if "login" in page.url.lower():
        log.error("❌ Login မအောင်မြင်ပါ။")
        return False, "Login failed: Still on login page"

    log.info("✅ Login successful")
    return True, "Login successful"

def auto_login():
    """Synchronous entry point for other modules"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(base_dir, "state.json")
    
    try:
        # Use run_task which handles the thread safety
        success, msg = browser_manager.run_task(_auto_login_task, save_state_path=state_path)
        return success, msg
    except Exception as e:
        log.error(f"❌ Auto Login Error: {str(e)}")
        return False, str(e)

async def perform_login_on_page(page):
    """Helper to perform login on an existing page object (to avoid deadlocks)"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(base_dir, "state.json")
    success, msg = await _auto_login_task(page)
    if success:
        await page.context.storage_state(path=state_path)
    return success, msg

if __name__ == "__main__":
    success, msg = auto_login()
    print(f"Result: {success}, Message: {msg}")
