# Version: 1.1 (Shared Browser Support)
import os
import sys
import asyncio

# 💡 Absolute Path Fix for Module
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import db_manager
from logger import log
from modules.browser_manager import browser_manager

async def _check_order_status_task(page, order_id):
    """Async task for order status check logic"""
    log.info(f"🔍 Checking status for Order ID: {order_id}")
    
    # --- Status Check Logic ---
    tracking_url = f"https://www.carrymanexpress.com/track?id={order_id}"
    log.info(f"🔗 {tracking_url} သို့ သွားနေပါသည်...")
    await page.goto(tracking_url)
    await page.wait_for_load_state('networkidle')
    
    # TODO: အမှန်တကယ် scraping လုပ်မည့် logic ကို ဤနေရာတွင် ထည့်သွင်းရန်
    # လက်ရှိတွင် multi-tab အလုပ်လုပ်ပုံကို စမ်းသပ်ရန် placeholder သာ ထည့်ထားပါသည်
    await asyncio.sleep(2) # Simulate work
    
    return True, f"Order {order_id} status check completed (Multi-tab test)."

def check_order_status(order_id):
    """Synchronous entry point for other modules"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(base_dir, "state.json")
    
    try:
        return browser_manager.run_task(
            _check_order_status_task, 
            storage_state=state_path,
            order_id=order_id
        )
    except Exception as e:
        log.error(f"❌ Error checking order {order_id}: {e}")
        return False, str(e)

def run(data, event):
    """
    Standard Module Entry Point
    data: dict (payload အချက်အလက်များ)
    event: str (လုပ်ဆောင်ရမည့် event အမည်)
    """
    if event == "check_status":
        order_id = data.get("order_id")
        if not order_id:
            return False, "Missing order_id in data"
            
        return check_order_status(order_id)
    
    return False, f"Unknown event: {event}"

def handle(bot, message):
    """
    Central Router မှတစ်ဆင့် ခေါ်ယူသည့် Entry Point
    """
    log.info(f"🔍 Check Order module handled message: {message.message_id}")

if __name__ == "__main__":
    # 🧪 Module ကို တိုက်ရိုက် စမ်းသပ်ရန်
    test_data = {"order_id": "CM123456"}
    success, message = run(test_data, "check_status")
    print(f"Result: {success}, Message: {message}")
