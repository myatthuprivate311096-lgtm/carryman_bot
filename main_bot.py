# Version: 6.0 (Receiver Process - Polling Only)
import os
import time
import telebot
from dotenv import load_dotenv
from logger import log
import db_manager
import commands_handler
import main_router
from modules import auditor
from handlers import alert_handler, pickup_handler, message_handler, fb_handler

# 💡 Absolute Path Fix for .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID'))
MANAGER_IDS = [int(i.strip()) for i in os.getenv('MANAGER_IDS', str(MANAGER_ID)).split(',')]

def is_manager(user_id):
    return user_id in MANAGER_IDS

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=20)


# Initialize DB (Explicit call for Worker 1)
db_manager.init_db()

# Register Commands & Handlers
auditor.set_bot(bot)
commands_handler.register_handlers(bot)
# /ai must register BEFORE catch-all message_handler (telebot uses first matching handler)
main_router.register_ai_handler(bot)
alert_handler.register_alert_handlers(bot, is_manager)
pickup_handler.register_pickup_handlers(bot)
message_handler.register_message_handlers(bot, is_manager)
fb_handler.register_fb_handlers(bot)

# 🚀 Stability & Auto-Recovery Polling
# ==========================================
def send_heartbeat():
    """HealthCheck URL သို့ Heartbeat ပို့ခြင်း"""
    url = os.getenv('HEALTHCHECK_URL')
    if url:
        try:
            import requests
            requests.get(url, timeout=10)
            log.info("💓 Heartbeat sent from Receiver.")
        except Exception as e:
            log.error(f"❌ Receiver Heartbeat Failed: {e}")

def start_bot():
    log.info("🚀 CarryMan Bot (Worker 1: Ingestion) is starting...")
    commands_handler.register_bot_commands(bot)

    # Heartbeat ကို background thread နဲ့ run ပါမယ်
    import threading, subprocess, json as _json
    def heartbeat_loop():
        while True:
            send_heartbeat()
            time.sleep(300) # ၅ မိနစ်တစ်ခါ
    
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    # 🛡️ Auditor Worker (SLA Alerts + Escalations)
    threading.Thread(target=auditor.process_audits, args=(bot,), daemon=True).start()
    log.info("🛡️ Auditor worker thread started in ingestion process.")

    # PM2-based health monitor is removed as it's redundant in Docker.

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
    log.info("📡 Receiver Process is starting (Polling Only)...")
    start_bot()