from playwright.sync_api import sync_playwright
import os
import sys
from logger import log

def manual_login():
    """
    Opens a browser in non-headless mode for manual login and saves the session state.
    """
    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
    login_url = "https://www.carrymanexpress.com/login"

    with sync_playwright() as p:
        log.info("🌐 Browser ကို non-headless mode ဖြင့် စတင်ဖွင့်နေပါပြီ...")
        # Launching browser in non-headless mode so user can see and interact
        browser = p.chromium.launch(headless=False)
        
        # Create a new context
        context = browser.new_context()
        page = context.new_page()

        try:
            log.info(f"🔗 {login_url} သို့ သွားနေပါသည်...")
            page.goto(login_url)
            
            print("\n" + "="*50)
            print("📢 အစ်ကို၊ Browser မှာ manual login အရင်ဝင်ပေးပါခင်ဗျာ။")
            print("📢 Login ဝင်ပြီးသွားပြီဆိုရင် ဒီ Terminal မှာ 'Enter' ခေါက်ပေးပါ။")
            print("="*50 + "\n")
            
            # Wait for user to press Enter in the terminal
            input("👉 Login ဝင်ပြီးရင် Enter ခေါက်ပါ...")

            # Save the storage state
            log.info(f"💾 Session state ကို {state_path} မှာ သိမ်းဆည်းနေပါသည်...")
            context.storage_state(path=state_path)
            log.info("✅ Session state ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။")

        except Exception as e:
            log.error(f"❌ အမှားအယွင်း ဖြစ်ပေါ်ခဲ့ပါသည်: {str(e)}")
        finally:
            browser.close()
            log.info("🌐 Browser ကို ပိတ်လိုက်ပါပြီ။")

if __name__ == "__main__":
    try:
        manual_login()
    except KeyboardInterrupt:
        print("\n👋 အစ်ကို၊ အစီအစဉ်ကို ရပ်ဆိုင်းလိုက်ပါပြီ။")
        sys.exit(0)
