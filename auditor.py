# Version: 2.1 (Worker 2: AI Brain - Standalone Auditor - Refactored)
import os
import time
import json
import html
import telebot
import pytz
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import log
import db_manager
from openai import OpenAI

# 💡 Absolute Path Fix
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID', 0))
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY')
RECORD_GROUP_ID = int(os.getenv('ARCHIVE_CHAT_ID', -1003601049225))
HEALTHCHECK_URL = os.getenv('HEALTHCHECK_URL')

# 🧪 Test Mode Configuration
TEST_GROUP_ID = int(os.getenv('TEST_GROUP_ID', 3539520778))

bot = telebot.TeleBot(BOT_TOKEN)

def is_office_hours(chat_id=None):
    """
    Asia/Yangon Timezone (UTC+6:30)
    Business Hours: 09:00 AM - 06:00 PM
    Grace Period: Start at 09:10 AM
    
    💡 Test Mode: TEST_GROUP_ID bypasses office hours (24/7)
    """
    if chat_id == TEST_GROUP_ID:
        return True

    tz = pytz.timezone('Asia/Yangon')
    mm_time = datetime.now(tz)
    
    current_hour = mm_time.hour
    current_minute = mm_time.minute
    
    # 09:10 AM to 05:59 PM
    # 💡 Logic ကို ပိုမိုတိကျစေရန် မိနစ်ဖြင့် တွက်ချက်ခြင်း
    total_minutes = current_hour * 60 + current_minute
    start_minutes = 9 * 60 + 10 # 09:10 AM
    end_minutes = 18 * 60      # 06:00 PM
    
    if start_minutes <= total_minutes < end_minutes:
        return True
    return False

def evaluate_with_ai(group_name, target_msgs_list, active_alerts, preceding_msgs, subsequent_msgs, chat_id, topic_id):
    """
    Gemini API ကိုသုံး၍ Message အစုအဝေး (Batch) ၏ အခြေအနေကို ဆုံးဖြတ်ခြင်း။
    target_msgs_list: [(msg_id, text, ts), ...]
    """
    if not GEMINI_API_KEY:
        log.error("❌ AI Key Missing (GEMINI_API_KEY or OPENROUTER_API_KEY)")
        return "NEW_ALERT", {"summary": "AI Key Missing"}

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=GEMINI_API_KEY
        )

        # Context တည်ဆောက်ခြင်း
        active_context = "\n".join([f"- AlertID: {a[0]} | Content: {a[2]}" for a in active_alerts]) if active_alerts else "None"
        preceding_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(p[1]) else 'Customer'} ({p[3]}): {p[0]}" for p in preceding_msgs]) if preceding_msgs else "None"
        
        # Target Messages (The batch to audit)
        targets_context = "\n".join([f"ID: {m[0]} | Text: {m[1]}" for m in target_msgs_list])

        # Subsequent Context (After the last message in target_msgs_list)
        filtered_subsequent = [s for s in subsequent_msgs if s[0] not in [a[2] for a in active_alerts]]
        subsequent_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(s[1]) else 'Customer'} ({s[3]}): {s[0]}" for s in filtered_subsequent]) if filtered_subsequent else "None"

        # 💡 Isolated Learning: Fetch past mistakes for this specific chat/topic
        past_mistakes = db_manager.get_isolated_feedback(chat_id, topic_id, limit=10)
        master_rules = db_manager.get_isolated_rules(chat_id, topic_id)
        
        mistakes_context = ""
        if past_mistakes:
            mistakes_context += "\n[Recent Feedback (Mistakes to Avoid)]:\n"
            for cat, txt in past_mistakes:
                mistakes_context += f"- Category: {cat} | Text: {txt}\n"
        
        rules_context = ""
        if master_rules:
            rules_context += "\n[Master Rules for this Topic]:\n"
            for rule in master_rules:
                rules_context += f"- {rule}\n"

        prompt = f"""
        Role: Senior Auditor for "{group_name}" Delivery Service.
        
        ## PAST MISTAKES TO AVOID
        {mistakes_context}
        {rules_context}

        Task: Audit a group of pending messages. Group them into a single ticket if they concern the same issue.

        [Preceding Context]:
        {preceding_context}

        [Target Messages to Audit (Batch)]:
        {targets_context}
        
        [Subsequent Conversation Flow]:
        {subsequent_context}

        [Active Alerts in this Topic]:
        {active_context}

        Rules:
        1. DUPLICATE/APPEND: If these messages concern the same issue as an existing 'Active Alert', return APPEND.
        2. AUTO-RESOLVE: If subsequent messages (especially from Staff or if they react with ❤️) show the issue is already handled, return RESOLVE.
        3. INTENT GROUPING: Group multiple messages into ONE ticket if they are related. Do NOT issue one ticket per line.
        4. IGNORE: If messages are just noise (thanks, ok, hi).

        Output ONLY JSON:
        {{
            "action": "NEW_ALERT" | "APPEND" | "RESOLVE" | "IGNORE",
            "target_alert_id": alert_msg_id (if APPEND, else null),
            "grouped_msg_ids": [list of msg_ids that belong to this ticket],
            "summary": "Combined Burmese summary of the issue",
            "category": "ငွေလွှဲ / ပစ္စည်းစုံစမ်း / လိပ်စာပြင် / အခြား",
            "intent": "ပစ္စည်းပို့ဆောင်ရေး" | "ငွေလွှဲ/ငွေပေးချေမှု" | "အထွေထွေစုံစမ်းမှု"
        }}
        """

        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=25.0
        )
        
        content = response.choices[0].message.content.strip()
        res_data = json.loads(content)
        
        # 💡 AI က တစ်ခါတရံ list အနေနဲ့ ပြန်ပေးတတ်လို့ dict ဖြစ်အောင် ညှိခြင်း
        if isinstance(res_data, list) and len(res_data) > 0:
            res_data = res_data[0]
            
        if not isinstance(res_data, dict):
            log.error(f"❌ AI returned invalid format: {type(res_data)}")
            return "NEW_ALERT", {"summary": "AI Format Error - Defaulting to New Alert"}

        action = res_data.get("action", "NEW_ALERT")
        
        # Safety Check: If AI says RESOLVE but no staff message exists
        if action == "RESOLVE" and not any(db_manager.check_if_staff(s[1]) for s in subsequent_msgs):
            action = "NEW_ALERT"
            
        return action, res_data
    except Exception as e:
        log.error(f"❌ Auditor AI Error for {group_name} ({chat_id}/{topic_id}): {e}")
        return "ERROR", {"summary": "AI Error - Retrying..."}

def get_routing_data(chat_id, topic_id, summary="", category="", intent=""):
    """
    ဗဟိုမှ ထိန်းချုပ်ခြင်း (Central Mapping)
    Workflow Step 5: Explicit Routing Table (os_groups) မှ Target Group/Topic ကို ရှာဖွေခြင်း
    """
    # ၁။ Explicit Routing Table (os_groups) မှ ရှာဖွေခြင်း
    route = db_manager.get_routing_entry(chat_id, topic_id)
    if route and route[0] is not None and route[1] is not None:
        log.info(f"🎯 Explicit Route Found: {chat_id}/{topic_id} -> {route[0]}/{route[1]}")
        return route[0], route[1]

    # ၂။ ရှာမတွေ့ပါက Manager ထံ အကြောင်းကြားရန် Status ပြန်ပေးခြင်း
    log.warning(f"⚠️ No Explicit Route for {chat_id}/{topic_id}. Notifying Manager...")
    return None, None

def notify_manager_missing_route(chat_id, topic_id, shop_name, trigger_text, ai_summary, original_msg_id=0):
    """ Routing မရှိသေးသော ဆိုင်အတွက် Manager ထံ ရွေးချယ်စရာ Button များ ပို့ပေးခြင်း """
    try:
        log.info(f"🔍 notify_manager_missing_route: shop={shop_name}, msg_id={original_msg_id}, chat={chat_id}")
        text = (
            f"🚨 **Missing Routing Rule**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{shop_name}</b>\n"
            f"🎧 Topic: <b>{topic_id}</b>\n"
            f"💬 စာသား: {html.escape(trigger_text[:100])}...\n"
            f"🤖 AI Summary: {html.escape(ai_summary)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ဤဆိုင်မှ Alert များအတွက် Target Topic ကို ရွေးပေးပါ-"
        )
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        
        # 💡 View Message Link
        if original_msg_id and original_msg_id != 0:
            clean_chat_id = str(chat_id).replace("-100", "")
            # 💡 tg:// protocol and https:// fallback
            msg_link = f"https://t.me/c/{clean_chat_id}/{original_msg_id}"
            markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))
        else:
            log.warning(f"⚠️ Cannot add View Message button: original_msg_id is {original_msg_id}")

        # 💡 callback_data format: setrt_{chat_id}_{topic_id}_{target_topic_id}_{original_msg_id}
        markup.add(telebot.types.InlineKeyboardButton("🚚 Pickup (Topic 1)", callback_data=f"setrt_{chat_id}_{topic_id}_1_{original_msg_id}"))
        markup.add(telebot.types.InlineKeyboardButton("💰 Finance (Topic 35)", callback_data=f"setrt_{chat_id}_{topic_id}_35_{original_msg_id}"))
        markup.add(telebot.types.InlineKeyboardButton("⚠️ Error (Topic 37)", callback_data=f"setrt_{chat_id}_{topic_id}_37_{original_msg_id}"))
        
        bot.send_message(MANAGER_ID, text, reply_markup=markup, parse_mode="HTML")
        log.info(f"📲 Missing route notification sent to Manager for {shop_name}")
    except Exception as e:
        log.error(f"❌ Failed to notify Manager for {shop_name} ({chat_id}): {e}")

def send_new_alert(chat_id, topic_id, original_msg_id, text, summary, shop_name, original_ts, category="အခြား", intent=None, media_id=None, title="⚠️ **Pending Alert (15 Mins)**"):
    """ ဝန်ထမ်း group ထံသို့ Alert အသစ်ပို့ခြင်း (View Message & Done Buttons ပါဝင်သည်) """
    # 💡 Ensure IDs are integers to prevent binding errors
    chat_id = int(chat_id)
    topic_id = int(topic_id)
    original_msg_id = int(original_msg_id)

    target_chat, target_topic = get_routing_data(chat_id, topic_id, summary=summary, category=category, intent=intent)
    
    if target_chat is None or target_topic is None:
        # Route မရှိရင် Manager ဆီ စာပို့ပြီး WAITING_ROUTE ထားမယ်
        # 💡 AI က ထုတ်ပေးထားတဲ့ summary, category, intent တွေကို သိမ်းထားမှ Manager ရွေးပြီးရင် ပြန်သုံးလို့ရမယ်
        db_manager.update_message_status(original_msg_id, chat_id, 'WAITING_ROUTE', topic_id=topic_id, category=category, intent=intent, summary=summary)
        notify_manager_missing_route(chat_id, topic_id, shop_name, text, summary, original_msg_id=original_msg_id)
        return None

    # အချိန်ကို မြန်မာစံတော်ချိန်ဖြင့် ပြောင်းလဲခြင်း
    tz = pytz.timezone('Asia/Yangon')
    orig_time = datetime.fromtimestamp(original_ts, tz).strftime('%Y-%m-%d %I:%M %p')

    # HTML Mode အတွက် စာသားများကို Escape လုပ်ခြင်း (Special characters ကြောင့် Error မတက်စေရန်)
    safe_shop = html.escape(shop_name)
    safe_summary = html.escape(summary)
    safe_text = html.escape(text)
    safe_category = html.escape(category)
    safe_intent = html.escape(intent if intent else 'အထွေထွေ')

    alert_text = (
        f"<b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏪 ဆိုင်: <b>{safe_shop}</b>\n"
        f"💬 စာသား: {safe_text}\n"
        f"⏰ အချိန်: {orig_time}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    # Buttons တည်ဆောက်ခြင်း
    clean_chat_id = str(chat_id).replace("-100", "")
    # 💡 tg:// protocol သုံးခြင်းဖြင့် Telegram App ထဲ တိုက်ရိုက်ပွင့်စေသည်
    msg_link = f"tg://privatepost?channel={clean_chat_id}&post={original_msg_id}"
    
    # 💡 Topic ID 0 ဖြစ်နေလျှင် None သို့ ပြောင်းလဲခြင်း (General Topic အတွက်)
    safe_target_topic = target_topic if target_topic != 0 else None
    
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
        telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{original_msg_id}_{chat_id}"),
        telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{original_msg_id}_{chat_id}")
    )
    
    try:
        if media_id:
            msg = bot.send_photo(
                target_chat,
                media_id,
                caption=alert_text,
                message_thread_id=safe_target_topic,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            msg = bot.send_message(
                target_chat,
                alert_text,
                message_thread_id=safe_target_topic,
                parse_mode="HTML",
                reply_markup=markup
            )
        db_manager.save_alert_tracking(original_msg_id, chat_id, msg.message_id, target_chat)
        db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED', topic_id=topic_id, category=category, intent=intent, summary=summary)
        log.info(f"📢 Alert sent for {original_msg_id} in {shop_name} to Central Group")
        return msg.message_id
    except Exception as e:
        # 💡 Topic ID မရှိသော Error (Bad Request: message thread not found) ဖြစ်ပါက General သို့ ပို့မည်
        if "message thread not found" in str(e).lower() and safe_target_topic is not None:
            log.warning(f"⚠️ Topic {safe_target_topic} not found in Central Group. Falling back to General.")
            try:
                if media_id:
                    msg = bot.send_photo(target_chat, media_id, caption=alert_text, parse_mode="HTML", reply_markup=markup)
                else:
                    msg = bot.send_message(target_chat, alert_text, parse_mode="HTML", reply_markup=markup)
                db_manager.save_alert_tracking(original_msg_id, chat_id, msg.message_id, target_chat)
                db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED', topic_id=topic_id, category=category, intent=intent, summary=summary)
                return msg.message_id
            except Exception as e2:
                log.error(f"❌ Final Fallback Failed: {e2}")
                return None
        
        log.error(f"❌ Failed to send alert: {e}")
        return None

def append_to_alert(target_alert_id, target_alert_chat, new_text, original_msg_id, chat_id, topic_id=None):
    """ ရှိပြီးသား Alert ထဲသို့ စာသွားပေါင်းခြင်း """
    try:
        # HTML Mode အတွက် Escape လုပ်ခြင်း
        safe_new_text = html.escape(new_text)

        # Buttons တည်ဆောက်ခြင်း (Update message မှာလည်း တန်းနှိပ်လို့ရအောင်)
        clean_chat_id = str(chat_id).replace("-100", "")
        # 💡 tg:// protocol သုံးခြင်းဖြင့် Telegram App ထဲ တိုက်ရိုက်ပွင့်စေသည်
        msg_link = f"tg://privatepost?channel={clean_chat_id}&post={original_msg_id}"
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
            telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{original_msg_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{original_msg_id}_{chat_id}")
        )

        msg = bot.send_message(
            target_alert_chat,
            f"➕ <b>Update for Alert:</b>\n{safe_new_text}",
            reply_to_message_id=target_alert_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
        # 💡 Linked Message ID ကို သိမ်းဆည်းခြင်း (နောက်မှ ပြန်ဖျက်နိုင်ရန်)
        db_manager.add_linked_msg_id(original_msg_id, chat_id, msg.message_id)
        db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED', topic_id=topic_id)
        log.info(f"➕ Appended message {original_msg_id} to alert {target_alert_id} (Topic: {topic_id})")
    except Exception as e:
        log.error(f"❌ Failed to append to alert: {e}")

def resolve_and_cleanup(msg_id, chat_id, shop_name, text, staff_name="AI/Staff", manual_resolve=False):
    """ Alert ကိုဖျက်ပြီး Record Group သို့ ပို့ခြင်း (View Message Button ပါဝင်သည်) """
    # 💡 အလုပ်ချိန်ပြင်ပဖြစ်ပါက Record မထုတ်ရန် (အစ်ကို့တောင်းဆိုချက်အရ)
    # သို့သော် Alert Cleanup ကိုတော့ ဆက်လုပ်ပေးရမည်
    # manual_resolve=True (Done Button/Reaction) ဖြစ်ပါက အချိန်မရွေး Record ထုတ်မည်
    is_office = is_office_hours(chat_id) or manual_resolve

    # ၁။ မူရင်းစာ ဝင်ခဲ့သည့် အချိန်နှင့် Topic ကို ရှာခြင်း
    conn = db_manager.get_connection()
    msg_data = conn.execute("SELECT timestamp, topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    conn.close()
    
    duration_str = "Unknown"
    topic_id = 0
    if msg_data:
        orig_ts, topic_id = msg_data
        diff_seconds = int(time.time()) - orig_ts
        
        # ကြာချိန်ကို နာရီ/မိနစ်ဖြင့် တွက်ချက်ခြင်း
        hours = diff_seconds // 3600
        minutes = (diff_seconds % 3600) // 60
        if hours > 0:
            duration_str = f"{hours}h {minutes}m"
        else:
            duration_str = f"{minutes} mins"

    # ၂။ Alert Message များကို ဖျက်ခြင်း (Full Cleanup)
    tracking = db_manager.get_alert_tracking(msg_id, chat_id)
    
    if tracking:
        alert_msg_id, alert_chat_id, _, esc_msg_id, linked_ids_json, linked_customer_ids_json = tracking
        
        # (က) မူရင်း Alert ကို ဖျက်ခြင်း (Level 1)
        try:
            bot.delete_message(alert_chat_id, alert_msg_id)
            log.info(f"🗑️ Deleted Level 1 alert {alert_msg_id}")
        except Exception as e:
            log.warning(f"⚠️ Failed to delete Level 1 alert {alert_msg_id}: {e}")
        
        # (ခ) Manager ဆီက Escalation စာကို ဖျက်ခြင်း (Level 2)
        if esc_msg_id:
            try:
                bot.delete_message(MANAGER_ID, esc_msg_id)
                log.info(f"🗑️ Deleted Level 2 escalation {esc_msg_id}")
            except Exception as e:
                log.warning(f"⚠️ Failed to delete Level 2 escalation {esc_msg_id}: {e}")
            
        # (ဂ) စာသွားပေါင်းထားသော (Append) စာများကို ဖျက်ခြင်း
        if linked_ids_json:
            linked_ids = json.loads(linked_ids_json)
            for l_id in linked_ids:
                try:
                    bot.delete_message(alert_chat_id, l_id)
                except Exception as e:
                    log.warning(f"⚠️ Failed to delete linked message {l_id}: {e}")
            log.info(f"🗑️ Deleted {len(linked_ids)} linked messages")
        
        db_manager.delete_alert_tracking(msg_id, chat_id)
    else:
        # 💡 Fix: Tracking မရှိရင်တောင် Alert message တွေ ရှိနေနိုင်သေးလို့ (ဥပမာ - Grouped Ticket ဖြစ်နေရင်)
        # Parent ID ကို ပြန်ရှာပြီး Cleanup လုပ်ပေးရမည်
        parent_id = db_manager.get_parent_msg_id(msg_id, chat_id)
        if parent_id != msg_id:
            log.info(f"🔗 Found parent {parent_id} for message {msg_id}. Retrying cleanup...")
            return resolve_and_cleanup(parent_id, chat_id, shop_name, text, staff_name, manual_resolve)
        
        log.info(f"ℹ️ No active alert tracking found for {msg_id} in {chat_id}.")

    # ၃။ Record Group သို့ ပို့ခြင်း (Alert တက်ခဲ့သော စာများကိုသာ Record ပို့မည်)
    # 💡 Fix: Resolved Record ကို သတ်မှတ်ထားသော Resolved Group သို့ ပို့ရန်
    # လက်ရှိ get_routing_data က Alert Group ကိုပဲ ပြန်ပေးနေတာကြောင့်
    # သီးသန့် Resolved Group ID ကို သုံးရပါမယ်။
    
    # အစ်ကို့တောင်းဆိုချက်အရ Resolved Group ID ကို သီးသန့်သတ်မှတ်ပါမည်
    RESOLVED_GROUP_ID = int(os.getenv('RESOLVED_GROUP_ID', -1003601049225))
    RESOLVED_TOPIC_ID = int(os.getenv('RESOLVED_TOPIC_ID', 4))
    
    # [ 🔗 View Message ] Button တည်ဆောက်ခြင်း
    clean_chat_id = str(chat_id).replace("-100", "")
    # 💡 tg:// protocol သုံးခြင်းဖြင့် Telegram App ထဲ တိုက်ရိုက်ပွင့်စေသည်
    msg_link = f"tg://privatepost?channel={clean_chat_id}&post={msg_id}"
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))
    
    # 💡 Strict Check: Alert တက်ခဲ့မှသာ Record ပို့မည်
    if tracking:
        try:
            if is_office:
                # မူရင်းစာဝင်သည့်အချိန်ကို Format ချခြင်း
                tz = pytz.timezone('Asia/Yangon')
                orig_time_str = datetime.fromtimestamp(orig_ts, tz).strftime('%Y-%m-%d %I:%M %p') if 'orig_ts' in locals() else datetime.now(tz).strftime('%Y-%m-%d %I:%M %p')

                record_text = (
                    f"✅ **RESOLVED RECORD**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏪 ဆိုင်: {shop_name}\n"
                    f"💬 မူရင်းစာ: {text if text != '[Unknown]' else 'စာသားရှာမတွေ့ပါ'}\n"
                    f"👤 ဖြေရှင်းသူ: {staff_name}\n"
                    f"⏳ ကြာချိန်: {duration_str if duration_str != 'Unknown' else 'ချက်ချင်း'}\n"
                    f"📅 အချိန်: {orig_time_str}"
                )
                bot.send_message(
                    RESOLVED_GROUP_ID,
                    record_text,
                    message_thread_id=RESOLVED_TOPIC_ID,
                    reply_markup=markup
                )
            else:
                log.info(f"🌙 Off-hours resolution for {msg_id}. Alert cleaned but record skipped.")
        except Exception as e:
            log.error(f"❌ Failed to log to record group: {e}")
    else:
        log.info(f"ℹ️ Message {msg_id} was not an active alert. Skipping record.")

def handle_escalation(msg_id, chat_id, shop_name, text, topic_id):
    """ ၃၀ မိနစ်ပြည့်ပါက Manager ထံ Escalation ပို့ခြင်း (Layout အပြည့်အစုံဖြင့်) """
    tracking = db_manager.get_alert_tracking(msg_id, chat_id)
    if tracking:
        # alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids
        _, _, created_at, esc_msg_id, _, _ = tracking
        
        # မူရင်းအချိန်ကို ရှာခြင်း
        conn = db_manager.get_connection()
        msg_data = conn.execute("SELECT timestamp FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        conn.close()
        
        if not msg_data:
            return
        
        orig_ts = msg_data[0]
        
        # Escalation မပို့ရသေးလျှင် သို့မဟုတ် ၃၀ မိနစ်ကျော်လျှင်
        if not esc_msg_id and (int(time.time()) - orig_ts >= 1800):
            try:
                tz = pytz.timezone('Asia/Yangon')
                orig_time = datetime.fromtimestamp(orig_ts, tz).strftime('%I:%M %p')

                # HTML Mode အတွက် Escape လုပ်ခြင်း
                safe_shop = html.escape(shop_name)
                safe_text = html.escape(text)

                esc_text = (
                    f"🔥 <b>Escalated Alert (30 Mins)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏪 ဆိုင်: <b>{safe_shop}</b>\n"
                    f"💬 စာသား: {safe_text}\n"
                    f"⏰ အချိန်: {orig_time}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                
                # Buttons တည်ဆောက်ခြင်း
                clean_chat_id = str(chat_id).replace("-100", "")
                # 💡 tg:// protocol သုံးခြင်းဖြင့် Telegram App ထဲ တိုက်ရိုက်ပွင့်စေသည်
                msg_link = f"tg://privatepost?channel={clean_chat_id}&post={msg_id}"
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                    telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{msg_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{msg_id}_{chat_id}")
                )

                msg = bot.send_message(MANAGER_ID, esc_text, reply_markup=markup, parse_mode="HTML")
                db_manager.update_alert_tracking_esc(msg_id, chat_id, msg.message_id)
                db_manager.update_message_status(msg_id, chat_id, 'ESCALATED', topic_id=topic_id)
                log.warning(f"🚨 Escalated {msg_id} to Manager")
            except Exception as e:
                log.error(f"❌ Escalation failed: {e}")

def backup_database():
    """ Database ဖိုင်ကို Manager ဆီသို့ Backup ပို့ပေးခြင်း """
    try:
        if not os.path.exists(db_manager.DB_FILE):
            return
            
        log.info("💾 Starting Automated Database Backup...")
        with open(db_manager.DB_FILE, 'rb') as f:
            bot.send_document(
                MANAGER_ID,
                f,
                caption=f"📅 **CarryMan DB Backup**\nအလိုအလျောက် သိမ်းဆည်းထားသော မှတ်တမ်း\nအချိန်: {datetime.now(pytz.timezone('Asia/Yangon')).strftime('%Y-%m-%d %I:%M %p')}"
            )
        log.info("✅ Database Backup sent to Manager.")
    except Exception as e:
        log.error(f"❌ Backup Error: {e}")

def send_performance_report():
    """ ဝန်ထမ်းစွမ်းဆောင်ရည် Weekly Report ကို Manager ထံ ပို့ပေးခြင်း """
    try:
        stats = db_manager.get_staff_stats(period="weekly")
        if not stats:
            return
            
        report = "📊 **Weekly Staff Performance Report**\n"
        report += "━━━━━━━━━━━━━━━━━━\n"
        for name, total, avg in stats:
            report += f"👤 {name}\n"
            report += f"   • Resolved: **{total}** tickets\n"
            report += f"   • Avg Time: **{round(avg, 1)}** mins\n\n"
        report += "━━━━━━━━━━━━━━━━━━\n"
        report += "💡 ဤမှတ်တမ်းသည် လွန်ခဲ့သော ၇ ရက်စာ ဖြစ်ပါသည်။"
        
        bot.send_message(MANAGER_ID, report, parse_mode="Markdown")
        log.info("✅ Weekly Performance Report sent to Manager.")
    except Exception as e:
        log.error(f"❌ Performance Report Failed: {e}")

def send_heartbeat():
    """ Health Monitoring (Heartbeat) Signal ပို့ခြင်း """
    if HEALTHCHECK_URL:
        try:
            requests.get(HEALTHCHECK_URL, timeout=10)
            log.info("💓 Heartbeat sent to HealthCheck.")
        except Exception as e:
            log.error(f"❌ Heartbeat Failed: {e}")

def process_audits():
    """ Main Auditor Loop (Refactored for Intent Grouping) """
    log.info("🧠 Auditor (Worker 2: AI Brain) is running...")
    last_backup_date = None
    last_report_date = None
    last_heartbeat_time = 0
    last_cleanup_time = 0
    
    while True:
        try:
            # ၁။ Heartbeat & Cleanup (Every 5 mins)
            if time.time() - last_heartbeat_time >= 300:
                send_heartbeat()
                last_heartbeat_time = time.time()
            
            # ၃၀ နာရီကျော်နေသော Alert များကို အလိုအလျောက် ရှင်းလင်းခြင်း
            if time.time() - last_cleanup_time >= 3600: # နာရီဝက်တစ်ခါ စစ်မည်
                db_manager.auto_resolve_stale_alerts(hours=30)
                last_cleanup_time = time.time()

            tz = pytz.timezone('Asia/Yangon')
            now_mm = datetime.now(tz)
            today_str = now_mm.strftime('%Y-%m-%d')
            
            if now_mm.hour == 9 and now_mm.minute < 10 and last_backup_date != today_str:
                backup_database()
                last_backup_date = today_str
                
            if now_mm.weekday() == 6 and now_mm.hour == 9 and 5 <= now_mm.minute < 15 and last_report_date != today_str:
                send_performance_report()
                last_report_date = today_str

            is_standard_office = is_office_hours()

            # ၁။ ၁၅ မိနစ်ကျော်နေသော Pending စာရှိသည့် Topic များကို ရှာမည်
            pending_topics = db_manager.get_pending_topics(minutes=15)
            
            # 💡 Selective Processing: အလုပ်ချိန်ပြင်ပဆိုလျှင် Test Group တစ်ခုတည်းကိုပဲ စစ်မည်
            # 💡 Bypass for Testing: အခုလောလောဆယ် အစ်ကိုစမ်းသပ်နိုင်အောင် အချိန်မရွေး Audit လုပ်ပေးပါမည်
            if not is_standard_office:
                # pending_topics = [t for t in pending_topics if t[0] == TEST_GROUP_ID]
                pass
            
            if not pending_topics and not is_standard_office:
                time.sleep(60)
                continue

            for chat_id, topic_id in pending_topics:
                # 💡 Get shop name early for logging context
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                # 💡 Smart Batching: Get ALL pending messages in this topic (even if < 15 mins)
                # This prevents multiple alerts for albums/burst messages
                msgs = db_manager.get_pending_messages(minutes=15, limit=20, chat_id=chat_id, topic_id=topic_id, all_pending=True)
                if not msgs: continue

                log.info(f"📂 Auditing Batch: {shop_name} (Topic {topic_id}) in Chat {chat_id} ({len(msgs)} messages)")
                
                # Trigger message (the oldest one)
                trigger_msg_id, _, _, trigger_text, trigger_ts, trigger_media_id = msgs[0]
                
                active_alerts = db_manager.get_active_alerts_for_group(chat_id, topic_id)
                preceding_msgs = db_manager.get_messages_before(chat_id, topic_id, trigger_msg_id)
                subsequent_msgs = db_manager.get_messages_after(chat_id, topic_id, msgs[-1][0]) # After the last message in batch

                # Mark all as AUDITING to prevent double processing
                # 💡 Cast to int to prevent "tuple binding" error
                safe_chat_id = int(chat_id)
                safe_topic_id = int(topic_id)
                
                for m in msgs:
                    db_manager.update_message_status(int(m[0]), safe_chat_id, 'AUDITING', topic_id=safe_topic_id)
                
                action, ai_res = evaluate_with_ai(shop_name, msgs, active_alerts, preceding_msgs, subsequent_msgs, safe_chat_id, safe_topic_id)
                
                grouped_ids = ai_res.get("grouped_msg_ids", [trigger_msg_id])
                # Ensure trigger_msg_id is in grouped_ids
                if trigger_msg_id not in grouped_ids: grouped_ids.append(trigger_msg_id)

                if action == "RESOLVE":
                    for mid in grouped_ids:
                        db_manager.update_message_status(mid, chat_id, 'RESOLVED', topic_id=topic_id)
                    resolve_and_cleanup(trigger_msg_id, chat_id, shop_name, trigger_text, "AI Auto-Resolve")
                    log.info(f"✨ AI Auto-Resolved Group: {grouped_ids}")

                elif action == "IGNORE":
                    for mid in grouped_ids:
                        db_manager.update_message_status(mid, chat_id, 'RESOLVED', topic_id=topic_id)
                    log.info(f"🔇 AI Ignored Group: {grouped_ids}")

                elif action == "APPEND" and ai_res.get("target_alert_id"):
                    target_chat, target_topic = get_routing_data(chat_id, topic_id, summary=ai_res.get("summary", ""), category=ai_res.get("category", ""), intent=ai_res.get("intent", ""))
                    
                    if target_chat and target_topic:
                        # Append the combined text of grouped messages
                        combined_text = "\n".join([m[3] for m in msgs if m[0] in grouped_ids])
                        append_to_alert(ai_res["target_alert_id"], target_chat, combined_text, trigger_msg_id, chat_id, topic_id=topic_id)
                        # Link other messages in the group
                        for mid in grouped_ids:
                            if mid != trigger_msg_id:
                                db_manager.add_linked_customer_id(trigger_msg_id, chat_id, mid)
                                db_manager.update_message_status(mid, chat_id, 'ALERTED', topic_id=topic_id, category=ai_res.get("category"), intent=ai_res.get("intent"))
                    else:
                        # Route မရှိရင် NEW_ALERT logic အတိုင်း Manager ဆီ စာပို့ဖို့ Status ပြန်ပြောင်းမယ်
                        for mid in msgs: db_manager.update_message_status(mid, chat_id, 'PENDING', topic_id=topic_id)

                elif action == "NEW_ALERT":
                    # Create one ticket for the group
                    combined_text = "\n".join([m[3] for m in msgs if m[0] in grouped_ids])
                    alert_id = send_new_alert(
                        chat_id, topic_id, trigger_msg_id, combined_text,
                        ai_res.get("summary", "New Grouped Issue"),
                        shop_name, trigger_ts,
                        category=ai_res.get("category", "အခြား"),
                        intent=ai_res.get("intent", "အထွေထွေစုံစမ်းမှု"),
                        media_id=trigger_media_id
                    )
                    # Link other messages to this alert tracking
                    if alert_id:
                        for mid in grouped_ids:
                            if mid != trigger_msg_id:
                                db_manager.add_linked_customer_id(trigger_msg_id, chat_id, mid)
                                db_manager.update_message_status(mid, chat_id, 'ALERTED', topic_id=topic_id)
                    else:
                        # Alert မပို့ဖြစ်ရင် (ဥပမာ - Route မရှိလို့ Manager ဆီ စာပို့လိုက်ရင်)
                        # Trigger message က WAITING_ROUTE ဖြစ်သွားပြီမို့လို့ ကျန်တဲ့စာတွေကိုလည်း WAITING_ROUTE ပြောင်းထားမယ်
                        for m in msgs:
                            mid = m[0]
                            if mid != trigger_msg_id:
                                db_manager.update_message_status(mid, chat_id, 'WAITING_ROUTE', topic_id=topic_id)

                # 💡 Safety Net: Group ထဲမှာ မပါဝင်ခဲ့တဲ့ ကျန်ရှိနေတဲ့ စာများကို PENDING ပြန်ပြောင်းပေးခြင်း
                for m in msgs:
                    mid = m[0]
                    if mid not in grouped_ids:
                        db_manager.update_message_status(mid, chat_id, 'PENDING', topic_id=topic_id)

                if action not in ["RESOLVE", "IGNORE", "APPEND", "NEW_ALERT"]:
                    # Rollback all if error
                    for m in msgs: db_manager.update_message_status(m[0], chat_id, 'PENDING', topic_id=topic_id)
                
                time.sleep(2)

            # ၂။ Escalation Check (ALERTED ဖြစ်နေတာ နာရီဝက်ကျော်ရင်)
            conn = db_manager.get_connection()
            alerted_msgs = conn.execute(
                "SELECT msg_id, chat_id, topic_id, text FROM message_logs WHERE status = 'ALERTED'"
            ).fetchall()
            conn.close()
            
            for m_id, c_id, t_id, txt in alerted_msgs:
                # Escalation လည်း အလုပ်ချိန်ပြင်ပဆိုလျှင် Test Group အတွက်ပဲ အလုပ်လုပ်မည်
                if not is_standard_office and c_id != TEST_GROUP_ID:
                    continue
                    
                _, _, s_name = db_manager.get_topic_context(c_id, t_id)
                handle_escalation(m_id, c_id, s_name, txt, t_id)

            time.sleep(30)

        except Exception as e:
            # 💡 Enhanced logging to identify which shop/chat/topic caused the crash
            context_parts = []
            if 'shop_name' in locals(): context_parts.append(f"Shop: {shop_name}")
            if 'chat_id' in locals(): context_parts.append(f"Chat: {chat_id}")
            if 'topic_id' in locals(): context_parts.append(f"Topic: {topic_id}")
            
            context = f" ({', '.join(context_parts)})" if context_parts else ""
            log.error(f"⚠️ Auditor Loop Error{context}: {e}")
            time.sleep(10)

if __name__ == "__main__":
    process_audits()
