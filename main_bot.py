# Version: 4.2 (The Ultimate Master - Fully Structured & Synced)
import os
import time
from dotenv import load_dotenv

# ၁။ Environment Variables များကို အရင်ဆုံး Load လုပ်မည်
load_dotenv()

from logger import log
import telebot
import db_manager
import alert_system
import commands_handler

# ၂။ 💡 Bot ကို အရင်ဆုံး မွေးဖွားရပါမည် (အရေးကြီးဆုံးအဆင့်)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID'))

bot = telebot.TeleBot(BOT_TOKEN)

# ၃။ စနစ်များကို စတင်နှိုးခြင်း (Bot ရှိမှသာ လှမ်းချိတ်မည်)
db_manager.init_db()
alert_system.register_handlers(bot)
commands_handler.register_handlers(bot)

# ==========================================
# 🛡️ ၄။ Gatekeeper (လုံခြုံရေး)
# ==========================================
@bot.message_handler(func=lambda m: m.chat.type == 'private' and not (m.from_user.id == MANAGER_ID or db_manager.check_if_staff(m.from_user.id)))
def block_strangers(message):
    pass 

# ==========================================
# 🧠 ၅။ Core Logic (SLA Engine & Smart Filtering)
# ==========================================
@bot.message_reaction_handler()
def handle_reaction(reaction):
    """ ဝန်ထမ်းမှ Reaction ပေးလျှင် Alert ကို ပိတ်သိမ်းခြင်း """
    user_id = reaction.user.id
    chat_id = reaction.chat.id
    
    if db_manager.check_if_staff(user_id) or user_id == MANAGER_ID:
        if db_manager.check_if_os_group(chat_id):
            # Database မှ ဝန်ထမ်းနာမည်အမှန်ကို ဆွဲယူမည်
            staff_data = db_manager.get_staff_info(user_id)
            staff_name = staff_data[1] if staff_data else reaction.user.first_name
            
            db_manager.resolve_message(reaction.message_id, chat_id, f"{staff_name} (Emoji)")
            alert_system.update_resolved_alerts(bot, reaction.message_id, chat_id, f"{staff_name} (Emoji)")


@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'voice', 'video', 'document'])
def handle_all_messages(message):
    """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ထိန်းချုပ်မည့် Main Engine """
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text or message.caption or "[Media Content]"

    # --- [ အပိုင်း ၁: Maintenance Mode စစ်ဆေးခြင်း ] ---
    bot_active = db_manager.get_setting('bot_active') == 'True'
    
    if not bot_active:
        if user_id == MANAGER_ID and message.text == '/on':
            pass # Manager က /on လို့ရိုက်ရင် commands_handler ထဲကို လွှတ်ပေးလိုက်မည်
        else:
            # ဝန်ထမ်းများက Command လာရိုက်လျှင် သို့မဟုတ် သီးသန့်လာပို့လျှင် Feedback ပြန်ပေးမည်
            if message.chat.type == 'private' or message.text.startswith('/'):
                bot.reply_to(message, "🤖 **CarryMan AI System:**\n\nစနစ်အား ခေတ္တပိတ်ထားပါသည်။ Manager မှ ပြန်လည်မဖွင့်မချင်း ခဏစောင့်ပေးပါဗျ။")
            return # စနစ်ပိတ်ထားသဖြင့် အောက်က Ticket မှတ်တဲ့အလုပ်တွေ ဆက်မလုပ်တော့ပါ

    # --- [ အပိုင်း ၂: Core SLA Logic (Ticket ဖမ်းခြင်း/ပိတ်ခြင်း) ] ---
    is_staff = db_manager.check_if_staff(user_id)
    is_manager = (user_id == MANAGER_ID)
    is_os_group = db_manager.check_if_os_group(chat_id)

    # (က) ဝန်ထမ်း သို့မဟုတ် Manager ၏ လုပ်ဆောင်ချက်
    if (is_staff or is_manager) and is_os_group:
        if message.reply_to_message:
            # 💡 Reply ဆွဲမှသာ Ticket ကို ပိတ်မည် (Accidental Clear ကို တားဆီးရန်)
            original_id = message.reply_to_message.message_id
            
            staff_data = db_manager.get_staff_info(user_id)
            staff_name = staff_data[1] if staff_data else message.from_user.first_name
            
            db_manager.resolve_message(original_id, chat_id, staff_name)
            alert_system.update_resolved_alerts(bot, original_id, chat_id, f"{staff_name} (Reply)")
        return # ဝန်ထမ်းပို့သောစာကို Pending အဖြစ် မမှတ်ပါ

    # (ခ) Customer ဆီမှ စာဝင်လာခြင်း (Smart Filter Applied)
    if is_os_group and not is_staff and not is_manager:
        # 💡 Bot များပို့သောစာနှင့် '/' ဖြင့်စသော Command များကို စစ်ထုတ်ခြင်း
        if not message.from_user.is_bot and not text.startswith('/'):
            topic_id = message.message_thread_id if message.is_topic_message else 0
            db_manager.log_message(message.message_id, chat_id, topic_id, user_id, text, message.date)
            log.info(f"📩 New Pending Message from {user_id} in {chat_id}")

# ==========================================
# 🚀 ၆။ Stability & Auto-Recovery Polling
# ==========================================
def start_bot():
    log.info("🚀 CarryMan Bot Master Engine (V4.2) is starting...")
    alert_system.start_watchdog(bot)
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            log.error(f"⚠️ Bot Polling Error: {e}")
            # Error တက်ပါက ၅ စက္ကန့်စောင့်ပြီး အလိုအလျောက် ပြန်ပွင့်စေခြင်း
            time.sleep(5)
            log.info("🔄 Reconnecting Bot...")

if __name__ == "__main__":
    start_bot()