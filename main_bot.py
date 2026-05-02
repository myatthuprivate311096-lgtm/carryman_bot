# Version: 5.1 (Worker 1: Data Ingestion Bot - Refactored)
import os
import time
import html
import telebot
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import log
import db_manager
import commands_handler
import main_router
from handlers import alert_handler, pickup_handler, message_handler
from modules import auditor, distiller
import threading

# 💡 Absolute Path Fix for .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID'))
MANAGER_IDS = [int(i.strip()) for i in os.getenv('MANAGER_IDS', str(MANAGER_ID)).split(',')]

def is_manager(user_id):
    return user_id in MANAGER_IDS

bot = telebot.TeleBot(BOT_TOKEN)


# Initialize DB (Explicit call for Worker 1)
db_manager.init_db()

# Register Commands & Handlers
commands_handler.register_handlers(bot)
alert_handler.register_alert_handlers(bot, is_manager)
pickup_handler.register_pickup_handlers(bot)
message_handler.register_message_handlers(bot, is_manager)

@bot.message_handler(commands=['ai'])
def handle_ai_command(message):
    """ /ai command handler for Smart AI Support """
    main_router.handle_ai_query(bot, message)

# 🚀 Stability & Auto-Recovery Polling
# ==========================================
def start_bot():
    log.info("🚀 CarryMan Bot (Worker 1: Ingestion) is starting...")
    while True:
        try:
            # skip_pending=False: လိုင်းကျနေတုန်းက ကျန်ခဲ့တဲ့စာတွေကိုပါ ပြန်ဖတ်ရန်
            # 💡 Reaction များ ဖမ်းမိစေရန် allowed_updates ထည့်သွင်းခြင်း
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=False, allowed_updates=telebot.util.update_types)
        except Exception as e:
            log.error(f"⚠️ Bot Polling Error: {e}")
            # If it's a conflict, wait a bit longer
            if "409" in str(e):
                log.warning("🔄 Conflict detected, waiting 20 seconds before retry...")
                # အဟောင်း သေချာသေသွားစေရန် ၂၀ စက္ကန့် စောင့်ပါမည်
                time.sleep(20)
            else:
                time.sleep(5)

if __name__ == "__main__":
    # 🧠 Start Daily AI Distiller in a background thread
    distiller_thread = threading.Thread(target=distiller.run_scheduler, daemon=True)
    distiller_thread.start()
    log.info("🧠 Daily AI Distiller thread started.")

    # 🚚 Start Auto Pickup Queue Worker
    from modules import auto_pickup
    pickup_thread = threading.Thread(target=auto_pickup.run_queue_worker, args=(bot,), daemon=True)
    pickup_thread.start()
    log.info("🚚 Auto Pickup Queue Worker thread started.")

    # 🛡️ Start Auditor (Worker 2: AI Brain) in a background thread
    # This prevents Conflict (409) by sharing the same bot instance
    auditor.set_bot(bot)
    auditor_thread = threading.Thread(target=auditor.process_audits, daemon=True)
    auditor_thread.start()
    log.info("🛡️ Auditor (AI Brain) thread started.")
    
    start_bot()
