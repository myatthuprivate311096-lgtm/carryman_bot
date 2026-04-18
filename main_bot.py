# Version: 5.0 (Worker 1: Data Ingestion Bot)
import os
import time
from dotenv import load_dotenv
from logger import log
import telebot
import db_manager

# 💡 Absolute Path Fix for .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID'))

bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()

# Initialize DB
db_manager.init_db()

# ==========================================
# 🧠 Core Logic (Data Ingestion & Auto-Resolve)
# ==========================================

@bot.message_reaction_handler(func=lambda reaction: reaction.emoji in ['👍', '✅'])
def handle_reaction(reaction):
    """ ဝန်ထမ်းမှ Reaction (👍 သို့မဟုတ် ✅) ပေးလျှင် Alert ကို ပိတ်သိမ်းခြင်း """
    try:
        user_id = reaction.user.id
        chat_id = reaction.chat.id
        
        if db_manager.check_if_staff(user_id) or user_id == MANAGER_ID:
            if db_manager.check_if_os_group(chat_id):
                staff_data = db_manager.get_staff_info(user_id)
                staff_name = staff_data[1] if staff_data else reaction.user.first_name
                
                db_manager.resolve_message(reaction.message_id, chat_id, staff_name, method='Reaction')
                log.info(f"✅ Message {reaction.message_id} marked as RESOLVED via Reaction by {staff_name}")
    except Exception as e:
        log.error(f"❌ Reaction Handler Error: {e}")

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'voice', 'video', 'document'])
def handle_all_messages(message):
    """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း """
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        text = message.text or message.caption or "[Media Content]"
        
        is_staff = db_manager.check_if_staff(user_id)
        is_manager = (user_id == MANAGER_ID)
        is_os_group = db_manager.check_if_os_group(chat_id)
        
        if is_os_group:
            topic_id = message.message_thread_id if message.is_topic_message else 0
            
            # Smart Polling အတွက် နောက်ဆုံးဖတ်ထားသော ID ကို အမြဲ Update လုပ်မည်
            db_manager.update_last_read_id(chat_id, topic_id, message.message_id)

            if is_staff or is_manager:
                # 💡 ဝန်ထမ်းမှ Reply ပြန်လျှင် ထိုစာကို RESOLVED အဖြစ် သတ်မှတ်မည်
                if message.reply_to_message and message.reply_to_message.message_id != message.message_thread_id:
                    original_id = message.reply_to_message.message_id
                    
                    staff_data = db_manager.get_staff_info(user_id)
                    staff_name = staff_data[1] if staff_data else message.from_user.first_name
                    
                    db_manager.resolve_message(original_id, chat_id, staff_name, method='Reply')
                    log.info(f"✅ Message {original_id} marked as RESOLVED via Reply by {staff_name}")
                return

            # Customer ဆီမှ စာဝင်လာခြင်း
            if not message.from_user.is_bot and not text.startswith('/'):
                db_manager.log_message(message.message_id, chat_id, topic_id, user_id, text, message.date)
                log.info(f"📩 New Pending Message from {user_id} in {chat_id} (Topic: {topic_id})")

    except Exception as e:
        log.error(f"❌ Message Handler Error: {e}")

# ==========================================
# 🚀 Stability & Auto-Recovery Polling
# ==========================================
def start_bot():
    log.info("🚀 CarryMan Bot (Worker 1: Ingestion) is starting...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=False)
        except Exception as e:
            log.error(f"⚠️ Bot Polling Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    start_bot()
