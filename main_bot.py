# Version: 5.1 (Worker 1: Data Ingestion Bot - Refactored)
import os
import time
import html
import telebot
import pytz
from datetime import datetime
from dotenv import load_dotenv
from logger import log
import db_manager
import commands_handler
import auditor

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

@bot.callback_query_handler(func=lambda call: call.data.startswith('setrt_'))
def handle_set_routing(call):
    """ Manager မှ Missing Route အတွက် Topic ရွေးချယ်ပေးခြင်းကို လက်ခံဆောင်ရွက်ခြင်း """
    try:
        user_id = call.from_user.id
        if user_id != MANAGER_ID:
            bot.answer_callback_query(call.id, "⚠️ Manager သာ လုပ်ဆောင်ခွင့်ရှိပါသည်။", show_alert=True)
            return

        # data format: setrt_{chat_id}_{topic_id}_{target_topic_id}_{original_msg_id}
        parts = call.data.split('_')
        chat_id = int(parts[1])
        topic_id = int(parts[2])
        target_topic = int(parts[3])
        original_msg_id = int(parts[4]) if len(parts) > 4 else 0
        target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))

        # ၁။ Database တွင် Routing Update လုပ်ခြင်း
        db_manager.update_routing_entry(chat_id, topic_id, target_chat, target_topic)

        # ၂။ ချက်ချင်း Alert ပို့ပေးခြင်း (အစ်ကို့တောင်းဆိုချက်အရ)
        log.info(f"🔍 Attempting to send immediate alert for msg_id: {original_msg_id}, chat: {chat_id}, topic: {topic_id}")
        if original_msg_id != 0:
            # WAITING_ROUTE ဖြစ်နေတဲ့ context ကို ပြန်ယူမယ်
            ctx = db_manager.get_message_context(original_msg_id, chat_id)
            if ctx:
                text, summary, category, intent, ts, media_id = ctx
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                log.info(f"📤 Sending alert to target topic {target_topic} for {shop_name}")
                # Alert ပို့မယ်
                auditor.send_new_alert(
                    chat_id, topic_id, original_msg_id, text, summary, shop_name, ts,
                    category=category, intent=intent, media_id=media_id
                )
                
                # တူတူ WAITING_ROUTE ဖြစ်နေတဲ့ တခြားစာတွေကိုလည်း ALERTED ပြောင်းပေးရမယ်
                conn = db_manager.get_connection()
                conn.execute(
                    "UPDATE message_logs SET status='ALERTED' WHERE chat_id=? AND topic_id=? AND status='WAITING_ROUTE'",
                    (chat_id, topic_id)
                )
                conn.commit()
                conn.close()

        # ၃။ အောင်မြင်ကြောင်း အကြောင်းပြန်ခြင်း (စာမကျန်စေရန် Message ကို ဖျက်လိုက်ပါမည်)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception as de:
            log.warning(f"⚠️ Could not delete routing confirmation message: {de}")

        bot.answer_callback_query(call.id, "✅ Routing Updated & Alert Sent!", show_alert=True)
        log.info(f"🎯 Manager set explicit route and sent alert for {chat_id}/{topic_id} -> {target_topic}")

    except Exception as e:
        log.error(f"❌ Set Routing Callback Error: {e}")
        bot.answer_callback_query(call.id, "❌ Error occurred")

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

            # ၁။ Alert Cleanup & Record Group သို့ ပို့ခြင်း (Archive to Topic 4)
            # 💡 resolve_and_cleanup ကို အရင်ခေါ်ရမည် (Tracking data မပျောက်ခင်)
            _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
            
            # 💡 resolve_and_cleanup ထဲတွင် Alert ဖျက်ခြင်းနှင့် Archive ပို့ခြင်းကို လုပ်ဆောင်မည်
            # manual_resolve=True ထည့်ပေးခြင်းဖြင့် Office Hours ပြင်ပဖြစ်စေ Record ပို့မည်
            auditor.resolve_and_cleanup(original_msg_id, chat_id, shop_name, orig_text, f"{staff_name} (Done Button)", manual_resolve=True)

            # ၂။ DB တွင် Resolve လုပ်ခြင်း
            db_manager.resolve_message(original_msg_id, chat_id, staff_name, method='Done Button', topic_id=topic_id)
            
            # ၃။ Button နှိပ်သူကို အကြောင်းပြန်ခြင်း
            bot.answer_callback_query(call.id, "✅ Resolved and Recorded!")
            
            # 💡 Safety: Tracking မရှိလို့ resolve_and_cleanup က မဖျက်မိပါက နှိပ်လိုက်တဲ့ message ကို တိုက်ရိုက်ဖျက်မည်
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
                
            log.info(f"✅ Message {original_msg_id} resolved via Done Button by {staff_name}")
        else:
            bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
    except Exception as e:
        log.error(f"❌ Done Button Error: {e}")
        bot.answer_callback_query(call.id, "❌ Error occurred")

@bot.callback_query_handler(func=lambda call: call.data.startswith('wrong_back_'))
def handle_wrong_back(call):
    """ Wrong Alert menu မှ မူလ menu သို့ ပြန်သွားခြင်း """
    try:
        parts = call.data.split('_')
        orig_id = parts[2]
        chat_id = parts[3]
        
        clean_chat_id = str(chat_id).replace("-100", "")
        # 💡 tg:// protocol သုံးခြင်းဖြင့် Telegram App ထဲ တိုက်ရိုက်ပွင့်စေသည်
        msg_link = f"tg://privatepost?channel={clean_chat_id}&post={orig_id}"
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
            telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{orig_id}_{chat_id}")
        )
        
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id)
    except Exception as e:
        log.error(f"❌ Wrong Back Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('wrong_'))
def handle_wrong_alert_button(call):
    """ Wrong Alert ခလုတ်ကို နှိပ်လိုက်လျှင် Option ၄ ခု ပြပေးခြင်း """
    try:
        user_id = call.from_user.id
        if not (db_manager.check_if_staff(user_id) or user_id == MANAGER_ID):
            bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
            return

        # data format: wrong_{original_msg_id}_{chat_id}
        parts = call.data.split('_')
        orig_id = parts[1]
        chat_id = parts[2]

        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            telebot.types.InlineKeyboardButton("👋 Greeting (နှုတ်ဆက်စာ)", callback_data=f"fb_greet_{orig_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("🔄 Wrong Topic (Topic မှားနေသည်)", callback_data=f"fb_topic_{orig_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("📑 Duplicate (ကိစ္စဟောင်း)", callback_data=f"fb_dup_{orig_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("✅ Already Resolved (ဖြေရှင်းပြီး)", callback_data=f"fb_done_{orig_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("🔙 Back", callback_data=f"wrong_back_{orig_id}_{chat_id}")
        )
        
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id)
    except Exception as e:
        log.error(f"❌ Wrong Alert Button Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('fb_'))
def handle_feedback_callback(call):
    """ Feedback Option တစ်ခုခုကို ရွေးလိုက်သည့်အခါ လုပ်ဆောင်ချက်များ """
    try:
        user_id = call.from_user.id
        parts = call.data.split('_')
        action = parts[1] # greet, topic, dup, done
        orig_id = int(parts[2])
        chat_id = int(parts[3])
        
        topic_id = db_manager.get_message_topic(orig_id, chat_id)
        _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
        
        conn = db_manager.get_connection()
        msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_id, chat_id)).fetchone()
        conn.close()
        orig_text = msg_data[0] if msg_data else "[Unknown]"

        if action == "topic":
            # Wrong Topic: Show routing options
            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                telebot.types.InlineKeyboardButton("🚚 Pickup (Topic 1)", callback_data=f"route_1_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("💰 Finance (Topic 35)", callback_data=f"route_35_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("⚠️ Error (Topic 37)", callback_data=f"route_37_{orig_id}_{chat_id}")
            )
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id)
            return

        # For Greet, Duplicate, Already Resolved:
        category_map = {
            "greet": "Greeting (နှုတ်ဆက်စာ)",
            "dup": "Duplicate (ကိစ္စဟောင်း)",
            "done": "Already Resolved (ဖြေရှင်းပြီး)"
        }
        category = category_map.get(action, "Other")
        
        # 💡 Already Resolved: Capture trailing messages for pattern learning
        if action == "done":
            trailing = db_manager.get_messages_after(chat_id, topic_id, orig_id, limit=3)
            if trailing:
                trailing_text = "\n[Trailing Pattern]:\n" + "\n".join([f"- {t[0]}" for t in trailing])
                orig_text += trailing_text

        # ၁။ Feedback သိမ်းဆည်းခြင်း (Isolated by chat/topic)
        db_manager.save_feedback(orig_id, chat_id, topic_id, category, orig_text, user_id)
        
        # ၂။ Alert Cleanup (Bypass Archive/Topic 4)
        # auditor.resolve_and_cleanup ထဲမှာ tracking ရှိမှ Archive ပို့တာဖြစ်လို့
        # tracking ကို အရင်ဖျက်လိုက်ရင် Archive bypass ဖြစ်သွားပါမယ်
        db_manager.delete_alert_tracking(orig_id, chat_id)
        
        # DB status update
        db_manager.update_message_status(orig_id, chat_id, 'RESOLVED' if action != "greet" else 'IGNORED', topic_id=topic_id)
        
        # Alert message ကို ဖျက်ခြင်း
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        
        bot.answer_callback_query(call.id, f"✅ Feedback Recorded: {category}")
        log.info(f"🎯 Feedback {action} recorded for {orig_id} by {user_id} (Archive Bypassed)")

    except Exception as e:
        log.error(f"❌ Feedback Callback Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('route_'))
def handle_rerouting(call):
    """ Wrong Topic အတွက် Re-routing လုပ်ဆောင်ခြင်း """
    try:
        parts = call.data.split('_')
        target_topic = int(parts[1])
        orig_id = int(parts[2])
        chat_id = int(parts[3])
        
        topic_id = db_manager.get_message_topic(orig_id, chat_id)
        _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
        
        conn = db_manager.get_connection()
        msg_data = conn.execute("SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_id, chat_id)).fetchone()
        conn.close()
        
        if msg_data:
            text, ts, media_id = msg_data
            # ၁။ Alert အဟောင်းကို ဖျက်ခြင်း
            tracking = db_manager.get_alert_tracking(orig_id, chat_id)
            if tracking:
                try: bot.delete_message(tracking[1], tracking[0])
                except: pass
                db_manager.delete_alert_tracking(orig_id, chat_id)

            # ၂။ Alert အသစ်ကို Target Topic ဆီ ပို့ခြင်း
            target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
            
            # အချိန်ပြောင်းလဲခြင်း
            tz = pytz.timezone('Asia/Yangon')
            orig_time = datetime.fromtimestamp(ts, tz).strftime('%Y-%m-%d %I:%M %p')

            # HTML Mode အတွက် Escape လုပ်ခြင်း
            safe_shop = html.escape(shop_name)
            safe_text = html.escape(text)

            alert_text = (
                f"🔄 <b>RE-ROUTED ALERT</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏪 ဆိုင်: <b>{safe_shop}</b>\n"
                f"💬 စာသား: {safe_text}\n"
                f"⏰ အချိန်: {orig_time}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            
            clean_chat_id = str(chat_id).replace("-100", "")
            # 💡 tg:// protocol သုံးခြင်းဖြင့် Telegram App ထဲ တိုက်ရိုက်ပွင့်စေသည်
            msg_link = f"tg://privatepost?channel={clean_chat_id}&post={orig_id}"
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{orig_id}_{chat_id}")
            )

            if media_id:
                msg = bot.send_photo(target_chat, media_id, caption=alert_text, message_thread_id=target_topic, parse_mode="HTML", reply_markup=markup)
            else:
                msg = bot.send_message(target_chat, alert_text, message_thread_id=target_topic, parse_mode="HTML", reply_markup=markup)
            
            db_manager.save_alert_tracking(orig_id, chat_id, msg.message_id, target_chat)
            db_manager.update_message_status(orig_id, chat_id, 'ALERTED', topic_id=topic_id)
            
            bot.answer_callback_query(call.id, f"✅ Re-routed to Topic {target_topic}")
            log.info(f"🔄 Re-routed {orig_id} to Topic {target_topic}")
            
            # Alert message အဟောင်း (ခလုတ်နှိပ်လိုက်တဲ့စာ) ကို ဖျက်ခြင်း
            try: bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass

    except Exception as e:
        log.error(f"❌ Re-routing Error: {e}")

@bot.message_reaction_handler(func=lambda message: True)
def handle_reaction(message):
    """ ဝန်ထမ်းမှ Reaction ပေးလျှင် Message ကို RESOLVED အဖြစ် သတ်မှတ်ခြင်း (Alert ရှိရှိ/မရှိရှိ) """
    try:
        reaction = message
        user_id = reaction.user.id
        chat_id = reaction.chat.id
        message_id = reaction.message_id
        
        if not reaction.new_reaction:
            return
            
        # 💡 ဘာ Reaction ပဲပေးပေး (Emoji ဖြစ်လျှင်) အလုပ်လုပ်မည်
        has_emoji = any(r.type == 'emoji' for r in reaction.new_reaction)
        if not has_emoji:
            return

        if db_manager.check_if_staff(user_id) or user_id == MANAGER_ID:
            staff_data = db_manager.get_staff_info(user_id)
            staff_name = staff_data[1] if staff_data else reaction.user.first_name
            
            # ပေးလိုက်သော emoji ကို ယူခြင်း (Logging အတွက်)
            emoji_used = next((r.emoji for r in reaction.new_reaction if r.type == 'emoji'), "Emoji")
            
            # 💡 Get topic_id from DB
            topic_id = db_manager.get_message_topic(message_id, chat_id)

            # 💡 Manual Alert ဖြစ်နေပါက Reaction ဖြင့် Resolve လုပ်ခွင့်မပေးပါ
            if db_manager.is_manual_alert(message_id, chat_id):
                return

            # ၁။ DB တွင် Resolve လုပ်ခြင်း (Alert မတက်ခင် ဖြစ်နိုင်သလို တက်ပြီးမှလည်း ဖြစ်နိုင်သည်)
            db_manager.resolve_message(message_id, chat_id, staff_name, method=f'Reaction ({emoji_used})', topic_id=topic_id)
            
            # ၂။ Alert Tracking ရှိမရှိ စစ်ဆေးပြီး Cleanup လုပ်ခြင်း
            tracking = db_manager.get_alert_tracking(message_id, chat_id)
            
            _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
            
            # မူရင်းစာသားကို db ကနေ ပြန်ယူရန်
            conn = db_manager.get_connection()
            msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (message_id, chat_id)).fetchone()
            conn.close()
            orig_text = msg_data[0] if msg_data else "[Unknown]"
            
            # resolve_and_cleanup သည် tracking မရှိလျှင် Archive မပို့ဘဲ Cleanup သာ လုပ်ပေးမည်
            # manual_resolve=True ထည့်ပေးခြင်းဖြင့် Office Hours ပြင်ပဖြစ်စေ Record ပို့မည်
            auditor.resolve_and_cleanup(message_id, chat_id, shop_name, orig_text, f"{staff_name} (Reaction {emoji_used})", manual_resolve=True)
            
            log.info(f"✅ Message {message_id} marked as RESOLVED via Reaction ({emoji_used}) by {staff_name}")
    except Exception as e:
        log.error(f"❌ Reaction Handler Error: {e}")

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'voice', 'video', 'document', 'video_note', 'audio'])
def handle_all_messages(message):
    """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း """
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        # 💡 Media Type အလိုက် စာသားပြောင်းလဲခြင်း
        media_id = None
        text = message.text or message.caption
        
        if not text:
            if message.photo:
                text = "🖼️ Photo"
                media_id = message.photo[-1].file_id
            elif message.voice:
                text = "🎙️ Voice Message"
                media_id = message.voice.file_id
            elif message.video:
                text = "📹 Video"
                media_id = message.video.file_id
            elif message.video_note:
                text = "🎥 Video Note"
                media_id = message.video_note.file_id
            elif message.document:
                text = f"📄 Document: {message.document.file_name}"
                media_id = message.document.file_id
            elif message.audio:
                text = f"🎵 Audio: {message.audio.title}"
                media_id = message.audio.file_id
            else:
                text = "📦 Media Content"
        
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
            # 💡 General Topic Fallback: 0 သို့မဟုတ် None ဖြစ်ပါက 1 ဟု သတ်မှတ်မည်
            topic_id = message.message_thread_id if (message.is_topic_message and message.message_thread_id) else 1
            
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
                    
                    # 💡 Manual Alert ဖြစ်နေပါက Reply ဖြင့် Resolve လုပ်ခွင့်မပေးပါ
                    if db_manager.is_manual_alert(original_id, chat_id):
                        return

                    # ၁။ DB တွင် Resolve လုပ်ခြင်း
                    db_manager.resolve_message(original_id, chat_id, staff_name, method='Reply', topic_id=orig_topic_id)
                    
                    # ၂။ Alert Cleanup & Record Group သို့ ပို့ခြင်း (Alert ရှိမှသာ ပို့မည်)
                    _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                    
                    # မူရင်းစာသားကို db ကနေ ပြန်ယူရန်
                    conn = db_manager.get_connection()
                    msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (original_id, chat_id)).fetchone()
                    conn.close()
                    orig_text = msg_data[0] if msg_data else "[Unknown]"
                    
                    # 💡 resolve_and_cleanup ထဲတွင် Alert ရှိ/မရှိ စစ်ဆေးပြီးသားဖြစ်သည်
                    # manual_resolve=True ထည့်ပေးခြင်းဖြင့် Office Hours ပြင်ပဖြစ်စေ Record ပို့မည်
                    auditor.resolve_and_cleanup(original_id, chat_id, shop_name, orig_text, f"{staff_name} (Reply)", manual_resolve=True)
                    
                    log.info(f"✅ Message {original_id} marked as RESOLVED via Reply by {staff_name}")
                return

            # Customer ဆီမှ စာဝင်လာခြင်း
            if not message.from_user.is_bot and not text.startswith('/'):
                db_manager.log_message(message.message_id, chat_id, topic_id, user_id, text, message.date, media_id=media_id)
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
