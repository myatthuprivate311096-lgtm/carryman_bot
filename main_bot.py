# Version: 5.0 (Worker 1: Data Ingestion Bot)
import os
import time
from dotenv import load_dotenv
from logger import log
import telebot
import db_manager
import commands_handler

# 💡 Absolute Path Fix for .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID'))

bot = telebot.TeleBot(BOT_TOKEN)

# Initialize DB (Explicit call for Worker 1)
db_manager.init_db()

# Register Commands
commands_handler.register_handlers(bot)

# ==========================================
# 🧠 Core Logic (Data Ingestion & Auto-Resolve)
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data.startswith('done_'))
def handle_done_button(call):
    """ Alert Message ရှိ Done Button ကို နှိပ်လိုက်လျှင် ဖြေရှင်းပြီးအဖြစ် သတ်မှတ်ခြင်း """
    try:
        user_id = call.from_user.id
        # data format: done_{original_msg_id}_{chat_id}
        parts = call.data.split('_')
        original_msg_id = int(parts[1])
        chat_id = int(parts[2])
        
        if db_manager.check_if_staff(user_id) or user_id == MANAGER_ID:
            staff_data = db_manager.get_staff_info(user_id)
            staff_name = staff_data[1] if staff_data else call.from_user.first_name
            
            # 💡 Get topic_id and original text from DB before resolving
            topic_id = db_manager.get_message_topic(original_msg_id, chat_id)
            
            conn = db_manager.get_connection()
            msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (original_msg_id, chat_id)).fetchone()
            conn.close()
            orig_text = msg_data[0] if msg_data else "[Unknown]"

            # ၁။ DB တွင် Resolve လုပ်ခြင်း
            db_manager.resolve_message(original_msg_id, chat_id, staff_name, method='Done Button', topic_id=topic_id)
            
            # ၂။ Alert Cleanup & Record Group သို့ ပို့ခြင်း (Archive to Topic 4)
            import auditor
            _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
            
            # 💡 resolve_and_cleanup ထဲတွင် Alert ဖျက်ခြင်းနှင့် Archive ပို့ခြင်းကို လုပ်ဆောင်မည်
            auditor.resolve_and_cleanup(original_msg_id, chat_id, shop_name, orig_text, f"{staff_name} (Done Button)")
            
            # ၃။ Button နှိပ်သူကို အကြောင်းပြန်ခြင်း
            bot.answer_callback_query(call.id, "✅ Resolved and Recorded!")
            log.info(f"✅ Message {original_msg_id} resolved via Done Button by {staff_name}")
        else:
            bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
    except Exception as e:
        log.error(f"❌ Done Button Error: {e}")

@bot.message_reaction_handler(func=lambda message: True)
def handle_reaction(message):
    """ ဝန်ထမ်းမှ ❤️ Reaction ပေးလျှင် Message ကို RESOLVED အဖြစ် သတ်မှတ်ခြင်း (Alert ရှိရှိ/မရှိရှိ) """
    try:
        reaction = message
        user_id = reaction.user.id
        chat_id = reaction.chat.id
        message_id = reaction.message_id
        
        if not reaction.new_reaction:
            return
            
        # 💡 ❤️ (heart) reaction ဖြစ်မှသာ အလုပ်လုပ်မည်
        is_heart = any(r.emoji == '❤️' for r in reaction.new_reaction if r.type == 'emoji')
        if not is_heart:
            return

        if db_manager.check_if_staff(user_id) or user_id == MANAGER_ID:
            staff_data = db_manager.get_staff_info(user_id)
            staff_name = staff_data[1] if staff_data else reaction.user.first_name
            
            # 💡 Get topic_id from DB
            topic_id = db_manager.get_message_topic(message_id, chat_id)

            # ၁။ DB တွင် Resolve လုပ်ခြင်း (Alert မတက်ခင် ဖြစ်နိုင်သလို တက်ပြီးမှလည်း ဖြစ်နိုင်သည်)
            db_manager.resolve_message(message_id, chat_id, staff_name, method='Reaction (❤️)', topic_id=topic_id)
            
            # ၂။ Alert Tracking ရှိမရှိ စစ်ဆေးပြီး Cleanup လုပ်ခြင်း
            tracking = db_manager.get_alert_tracking(message_id, chat_id)
            
            import auditor
            _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
            
            # မူရင်းစာသားကို db ကနေ ပြန်ယူရန်
            conn = db_manager.get_connection()
            msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (message_id, chat_id)).fetchone()
            conn.close()
            orig_text = msg_data[0] if msg_data else "[Unknown]"
            
            # resolve_and_cleanup သည် tracking မရှိလျှင် Archive မပို့ဘဲ Cleanup သာ လုပ်ပေးမည်
            auditor.resolve_and_cleanup(message_id, chat_id, shop_name, orig_text, f"{staff_name} (Reaction ❤️)")
            
            log.info(f"✅ Message {message_id} marked as RESOLVED via Reaction (❤️) by {staff_name}")
    except Exception as e:
        log.error(f"❌ Reaction Handler Error: {e}")

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'voice', 'video', 'document'])
def handle_all_messages(message):
    """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း """
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        text = message.text or message.caption or "[Media Content]"
        
        # 💡 ဝန်ထမ်းဖြစ်ကြောင်း စစ်ဆေးခြင်း (Database + Anonymous Admin + Group Owner)
        is_staff = db_manager.check_if_staff(user_id)
        is_manager = (user_id == MANAGER_ID)
        
        # Anonymous Admin သို့မဟုတ် Group Owner စစ်ဆေးခြင်း
        if not is_staff and not is_manager:
            if message.sender_chat and message.sender_chat.id == chat_id:
                is_staff = True # Anonymous Admin
            elif message.from_user and message.from_user.is_bot:
                return # Bot စာများကို ကျော်မည်
            else:
                # Admin List ထဲတွင် ပါ/မပါ စစ်ဆေးခြင်း (Safety Net)
                try:
                    member = bot.get_chat_member(chat_id, user_id)
                    if member.status in ['administrator', 'creator']:
                        is_staff = True
                except: pass

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
                    staff_name = staff_data[1] if staff_data else (message.from_user.first_name if message.from_user else "Staff")
                    
                    # 💡 Get the actual topic_id of the original message
                    orig_topic_id = db_manager.get_message_topic(original_id, chat_id)
                    
                    # ၁။ DB တွင် Resolve လုပ်ခြင်း
                    db_manager.resolve_message(original_id, chat_id, staff_name, method='Reply', topic_id=orig_topic_id)
                    
                    # ၂။ Alert Cleanup & Record Group သို့ ပို့ခြင်း (Alert ရှိမှသာ ပို့မည်)
                    import auditor
                    _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                    
                    # မူရင်းစာသားကို db ကနေ ပြန်ယူရန်
                    conn = db_manager.get_connection()
                    msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (original_id, chat_id)).fetchone()
                    conn.close()
                    orig_text = msg_data[0] if msg_data else "[Unknown]"
                    
                    # 💡 resolve_and_cleanup ထဲတွင် Alert ရှိ/မရှိ စစ်ဆေးပြီးသားဖြစ်သည်
                    auditor.resolve_and_cleanup(original_id, chat_id, shop_name, orig_text, f"{staff_name} (Reply)")
                    
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
    start_bot()
