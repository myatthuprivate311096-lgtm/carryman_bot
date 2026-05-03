# Version: 6.0 (Worker Process - Background Tasks Only)
import os
import time
import telebot
import threading
from dotenv import load_dotenv
from logger import log
import db_manager
from modules import auditor, distiller, auto_pickup

# 💡 Absolute Path Fix for .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

def run_worker():
    log.info("⚙️ Worker Process (Background Tasks) is starting...")
    
    # 🧠 Start Daily AI Distiller in a background thread
    distiller_thread = threading.Thread(target=distiller.run_scheduler, daemon=True)
    distiller_thread.start()
    log.info("🧠 Daily AI Distiller thread started.")

    # 🚚 Start Auto Pickup Queue Worker
    pickup_thread = threading.Thread(target=auto_pickup.run_queue_worker, args=(bot,), daemon=True)
    pickup_thread.start()
    log.info("🚚 Auto Pickup Queue Worker thread started.")

    # 🛡️ Start Auditor (Worker 2: AI Brain)
    # Note: This process does NOT poll, it only sends messages.
    auditor.set_bot(bot)
    log.info("🛡️ Auditor (AI Brain) starting...")
    auditor.process_audits() # This is usually a while True loop

if __name__ == "__main__":
    try:
        run_worker()
    except KeyboardInterrupt:
        log.info("🛑 Worker Process stopped by user.")
    except Exception as e:
        log.error(f"❌ Worker Process Fatal Error: {e}")
        time.sleep(10)
