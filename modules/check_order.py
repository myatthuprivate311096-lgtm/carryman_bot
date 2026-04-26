# Version: 1.0 (Standard Module Template)
import os
import sys

# 💡 Absolute Path Fix for Module
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import db_manager
from logger import log

def check_order_status(order_id):
    """
    Order status ကို စစ်ဆေးသည့် logic (ဥပမာ - Website မှ scraping လုပ်ခြင်း)
    """
    log.info(f"🔍 Checking status for Order ID: {order_id}")
    try:
        # TODO: Implement actual scraping or API call logic here
        # Example result
        return True, f"Order {order_id} is currently being processed."
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
    # bot.reply_to(message, "🔎 Check Order Module is looking up your order...")

if __name__ == "__main__":
    # 🧪 Module ကို တိုက်ရိုက် စမ်းသပ်ရန်
    test_data = {"order_id": "CM123456"}
    success, message = run(test_data, "check_status")
    print(f"Result: {success}, Message: {message}")
