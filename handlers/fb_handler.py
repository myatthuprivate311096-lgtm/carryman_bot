import telebot
import requests
import os
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from logger import log
import db_manager

# Load environment variables immediately
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN')
TARGET_GROUP_ID = os.getenv('FB_TARGET_GROUP_ID')

def register_fb_handlers(bot: telebot.TeleBot):
    log.info("✅ FB Handlers Registered")
    
    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith('fbc_'))
    def handle_fb_callbacks(call):
        log.info(f"🔘 FB Callback Received: {call.data} from {call.from_user.id} ({call.from_user.first_name})")
        staff_id = call.from_user.id
        staff_name = call.from_user.first_name
        
        if call.data.startswith('fbc_chat_'):
            fb_user_id = call.data.replace('fbc_chat_', '')
            
            # Check if staff is already busy
            busy_user = db_manager.get_active_fb_session(staff_id)
            if busy_user:
                bot.answer_callback_query(call.id, f"⚠️ အစ်ကို {busy_user} နဲ့ စကားပြောနေတုန်းမို့ အရင်ဆုံး Done နှိပ်ပေးပါခင်ဗျာ။", show_alert=True)
                return

            # Start Session
            db_manager.start_fb_session(staff_id, fb_user_id)
            db_manager.update_fb_task_status(fb_user_id, 'IN_CHAT', staff_id, staff_name)
            
            # Update Group Message
            task = db_manager.get_fb_task(fb_user_id)
            if task:
                user_display = task.get('fb_user_name') or fb_user_id
                updated_text = f"🔵 **Facebook Messenger**\n👤 Name: **{user_display}**\n💬 Message: {task['last_text']}\n\n👨‍💻 **Chat by {staff_name}**"
                try:
                    bot.edit_message_text(updated_text, TARGET_GROUP_ID, task['tg_group_msg_id'], parse_mode='Markdown')
                except Exception as e:
                    log.error(f"Error editing group msg: {e}")
            
            # Send Private Message to Staff
            user_display = task.get('fb_user_name') or fb_user_id if task else fb_user_id
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ Done", callback_data=f"fbc_done_{fb_user_id}"))
            
            welcome_msg = (
                f"🚀 **Chat Started with {user_display}**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💬 **Last Message:**\n{task['last_text'] if task else ''}\n\n"
                f"အခုကစပြီး အစ်ကိုပို့သမျှစာတွေ Facebook ဆီ တိုက်ရိုက်ရောက်ပါမယ်။"
            )
            
            # Send message to staff
            bot.send_message(staff_id, welcome_msg, reply_markup=markup, parse_mode='Markdown')
            
            # Answer callback without URL to avoid URL_INVALID error
            bot_user = bot.get_me()
            bot.answer_callback_query(call.id, f"Chat စတင်ပါပြီ။ {bot_user.first_name} ဆီသို့ သွားပါ အစ်ကို။")

        elif call.data.startswith('fbc_done_'):
            fb_user_id = call.data.replace('fbc_done_', '')
            task = db_manager.get_fb_task(fb_user_id)
            
            if task:
                user_display = task.get('fb_user_name') or fb_user_id
                # Cleanup Group
                try: bot.delete_message(TARGET_GROUP_ID, task['tg_group_msg_id'])
                except: pass
                
                # Cleanup Staff Session
                if task['staff_id']:
                    db_manager.end_fb_session(task['staff_id'])
                    # Delete the "Done" button message in private chat
                    try: bot.delete_message(task['staff_id'], call.message.message_id)
                    except: pass
                
                db_manager.delete_fb_task(fb_user_id)
                bot.answer_callback_query(call.id, "Task ကို ပိတ်လိုက်ပါပြီ။")

    @bot.message_handler(func=lambda m: m.chat.type == 'private')
    def handle_fb_private_reply(message):
        staff_id = message.from_user.id
        fb_user_id = db_manager.get_active_fb_session(staff_id)
        
        if fb_user_id:
            # Forward to Facebook
            success = send_fb_message(fb_user_id, message.text)
            if not success:
                bot.reply_to(message, "❌ Facebook ဆီ စာပို့လို့ မရပါဘူး။ Token စစ်ပေးပါ အစ်ကို။")
        else:
            # If not in FB session, let other handlers handle it (or ignore)
            pass

def send_fb_message(fb_user_id, text):
    if not FB_PAGE_ACCESS_TOKEN:
        log.error("FB_PAGE_ACCESS_TOKEN not set")
        return False
        
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": fb_user_id},
        "message": {"text": text}
    }
    try:
        r = requests.post(url, json=payload)
        if r.status_code != 200:
            log.error(f"FB API Error: {r.text}")
        return r.status_code == 200
    except Exception as e:
        log.error(f"FB Request Error: {e}")
        return False
