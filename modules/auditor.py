# Version: 2.2 (Module Standardized)
import os
import time
import json
import html
import telebot
import pytz
import requests
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 💡 Absolute Path Fix for Module
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import ai_utils
import db_manager
from logger import log

load_dotenv(os.path.join(BASE_DIR, '.env'))

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID', 0))
MANAGER_IDS = [int(i.strip()) for i in os.getenv('MANAGER_IDS', str(MANAGER_ID)).split(',')]

def is_manager(user_id):
    return user_id in MANAGER_IDS

RECORD_GROUP_ID = int(os.getenv('ARCHIVE_CHAT_ID', -1003906164269))
HEALTHCHECK_URL = os.getenv('HEALTHCHECK_URL')

# 🚨 Escalation Configuration
ESCALATION_GROUP_ID = int(os.getenv('ESCALATION_GROUP_ID', -1003906164269))
ESCALATION_TOPIC_ID = int(os.getenv('ESCALATION_TOPIC_ID', 5))

# 🧪 Test Mode Configuration
TEST_GROUP_ID = int(os.getenv('TEST_GROUP_ID', -1003539520778))

# 🚨 Escalation Configuration
ESCALATION_GROUP_ID = int(os.getenv('ESCALATION_GROUP_ID', -1003906164269))
ESCALATION_TOPIC_ID = int(os.getenv('ESCALATION_TOPIC_ID', 5))

_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

def is_office_hours(chat_id=None):
    """
    အလုပ်ချိန်အတွင်းဖြစ်မဖြစ် စစ်ဆေးခြင်း (09:00 AM - 06:15 PM MMT)
    """
    import pytz
    from datetime import datetime
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    current_time = now.hour * 100 + now.minute
    
    # 09:00 AM (900) to 06:15 PM (1815)
    is_office = 900 <= current_time < 1815
    
    if not is_office:
        log.info(f"🌙 Outside Office Hours: {now.strftime('%I:%M %p')} MMT. Alerts are paused.")
    
    return is_office

def evaluate_with_ai(group_name, target_msgs_list, active_alerts, preceding_msgs, subsequent_msgs, chat_id, topic_id):
    try:
        active_context = "\n".join([f"- AlertID: {a[0]} | Content: {a[2]}" for a in active_alerts]) if active_alerts else "None"
        preceding_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(p[1]) else 'Customer'} ({p[3]}): {p[0]}" for p in preceding_msgs]) if preceding_msgs else "None"
        targets_context = "\n".join([f"ID: {m[0]} | Text: {m[1]}" for m in target_msgs_list])
        filtered_subsequent = [s for s in subsequent_msgs if s[0] not in [a[2] for a in active_alerts]]
        subsequent_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(s[1]) else 'Customer'} ({s[3]}): {s[0]}" for s in filtered_subsequent]) if filtered_subsequent else "None"

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
        Strict Persona & Tone: 'You are a humble, professional, and respectful Myanmar Online Shop (OS) admin. You MUST strictly follow the tone, style, and examples provided in the OS Tone_&_Example data. Keep answers short, direct, and natural. NEVER use generic AI fluff like "Welcome to...", "If you need more info...", or "I am an AI assistant". STRICTLY PROHIBITED: Do NOT use the thumbs-up emoji (👍). Use the polite praying hands emoji (🙏) instead if needed.'
        
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
        2. AUTO-RESOLVE: ONLY return RESOLVE if there is a CLEAR reply or reaction from a STAFF member in the [Subsequent Conversation Flow] that addresses the issue. If only the customer is talking, NEVER return RESOLVE.
        3. INTENT GROUPING: Group multiple messages into ONE ticket if they are related. Do NOT issue one ticket per line.
        4. IGNORE: ONLY return IGNORE for pure noise (e.g., "hi", "hello", "thanks", "ok", "sticker", "emoji").
        5. STATUS INQUIRIES: Messages asking about delivery status, payment status, or "have you sent it yet?" (e.g., "အထုတ်တွေပို့ပြီးပြီလား", "ရောက်ပြီလား", "ဘယ်တော့ပို့မှာလဲ") are CRITICAL. NEVER return IGNORE or RESOLVE for these unless a staff has already replied.

        Output ONLY JSON:
        {{
            "action": "NEW_ALERT" | "APPEND" | "RESOLVE" | "IGNORE",
            "target_alert_id": alert_msg_id (if APPEND, else null),
            "grouped_msg_ids": [list of msg_ids that belong to this ticket],
            "summary": "Combined Burmese summary of the issue (Strictly follow OS Tone, maintain a humble and respectful persona, concise human-like paragraph)",
            "category": "ငွေလွှဲ / ပစ္စည်းစုံစမ်း / လိပ်စာပြင် / အခြား",
            "intent": "ပစ္စည်းပို့ဆောင်ရေး" | "ငွေလွှဲ/ငွေပေးချေမှု" | "အထွေထွေစုံစမ်းမှု"
        }}
        """

        content = ai_utils.get_ai_completion(prompt, response_format={"type": "json_object"}, timeout=25.0)
        if not content:
            return "NEW_ALERT", {"summary": "AI System Failure - Defaulting to New Alert"}
        res_data = json.loads(content)
        
        if isinstance(res_data, list) and len(res_data) > 0:
            res_data = res_data[0]
            
        if not isinstance(res_data, dict):
            log.error(f"❌ AI returned invalid format: {type(res_data)}")
            return "NEW_ALERT", {"summary": "AI Format Error - Defaulting to New Alert"}

        action = res_data.get("action", "NEW_ALERT")
        
        # 🛡️ Safety Net: ဝန်ထမ်းဘက်က စာပြန်တာမျိုး မရှိဘဲ RESOLVE သို့မဟုတ် IGNORE လုပ်ခြင်းကို တားဆီးရန်
        has_staff_reply = any(db_manager.check_if_staff(s[1]) for s in subsequent_msgs)
        
        if action in ["RESOLVE", "IGNORE"] and not has_staff_reply:
            # စာသားထဲတွင် စုံစမ်းသည့် စကားလုံးများ ပါ/မပါ ထပ်မံစစ်ဆေးခြင်း (Heuristic Safety)
            inquiry_keywords = ["ပို့ပြီးပြီလား", "ရောက်ပြီလား", "ဘယ်တော့", "မရသေးဘူး", "ကြာနေ", "sent", "status", "tracking"]
            is_inquiry = any(kw in targets_context for kw in inquiry_keywords)
            
            if is_inquiry:
                log.info(f"🛡️ Safety Net Triggered: Inquiry detected without staff reply. Overriding {action} to NEW_ALERT.")
                action = "NEW_ALERT"
            elif action == "RESOLVE":
                action = "NEW_ALERT"
            
        log.info(f"🤖 AI Decision for {group_name}: {action} (Staff Reply: {has_staff_reply})")
        return action, res_data
    except Exception as e:
        log.error(f"❌ Auditor AI Error for {group_name} ({chat_id}/{topic_id}): {e}")
        # 💡 Fallback Logic: AI Error တက်ရင်လည်း Alert မလွတ်သွားအောင် NEW_ALERT အဖြစ် ပို့ပေးမည်
        return "NEW_ALERT", {
            "summary": "AI Error Fallback (Manual Check Required)",
            "category": "အခြား",
            "intent": "အထွေထွေစုံစမ်းမှု"
        }

def get_routing_data(chat_id, topic_id, category="", intent=""):
    route = db_manager.get_routing_entry(chat_id, topic_id)
    if route and route[0] is not None and route[1] is not None:
        log.info(f"🎯 Explicit Route Found: {chat_id}/{topic_id} -> {route[0]}/{route[1]}")
        return route[0], route[1]

    log.warning(f"⚠️ No Explicit Route for {chat_id}/{topic_id}. Notifying Manager...")
    return None, None

def notify_manager_missing_route(chat_id, topic_id, shop_name, trigger_text, original_msg_id=0):
    try:
        log.info(f"🔍 notify_manager_missing_route: shop={shop_name}, msg_id={original_msg_id}, chat={chat_id}")
        text = (
            f"🚨 **Missing Routing Rule**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{shop_name}</b>\n"
            f"🎧 Topic: <b>{topic_id}</b>\n"
            f"💬 စာသား: {html.escape(trigger_text[:100])}...\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ဤဆိုင်မှ Alert များအတွက် Target Topic ကို ရွေးပေးပါ-"
        )
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        
        if original_msg_id and original_msg_id != 0:
            clean_chat_id = str(chat_id).replace("-100", "")
            msg_link = f"https://t.me/c/{clean_chat_id}/{original_msg_id}"
            markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))

        markup.add(telebot.types.InlineKeyboardButton("🚚 Pickup (Topic 1)", callback_data=f"setrt_{chat_id}_{topic_id}_1_{original_msg_id}"))
        markup.add(telebot.types.InlineKeyboardButton("💰 Finance (Topic 35)", callback_data=f"setrt_{chat_id}_{topic_id}_35_{original_msg_id}"))
        markup.add(telebot.types.InlineKeyboardButton("⚠️ Error (Topic 37)", callback_data=f"setrt_{chat_id}_{topic_id}_37_{original_msg_id}"))
        
        _bot.send_message(ESCALATION_GROUP_ID, text, message_thread_id=ESCALATION_TOPIC_ID, reply_markup=markup, parse_mode="HTML")
        log.info(f"📲 Missing route notification sent to Escalation Group for {shop_name}")
    except Exception as e:
        log.error(f"❌ Failed to notify Escalation Group for {shop_name} ({chat_id}): {e}")

def send_new_alert(chat_id, topic_id, original_msg_id, text, summary, shop_name, original_ts, category="အခြား", intent=None, media_id=None, title="⚠️ **15-Minute SLA Alert!**", force=False):
    chat_id = int(chat_id)
    topic_id = int(topic_id)
    original_msg_id = int(original_msg_id)

    log.info(f"📢 send_new_alert triggered: chat={chat_id}, topic={topic_id}, msg={original_msg_id}, force={force}")

    target_chat, target_topic = get_routing_data(chat_id, topic_id, category=category, intent=intent)
    
    if target_chat is None or target_topic is None:
        log.warning(f"⚠️ No route found for {shop_name} ({chat_id}/{topic_id}). Notifying Manager.")
        db_manager.update_message_status(original_msg_id, chat_id, 'WAITING_ROUTE', topic_id=topic_id, category=category, intent=intent, summary=summary)
        notify_manager_missing_route(chat_id, topic_id, shop_name, text, original_msg_id=original_msg_id)
        return "WAITING_ROUTE"

    if not force and not is_office_hours(chat_id):
        log.info(f"🌙 Office Hours Over: Skipping alert for {shop_name} (Route exists)")
        db_manager.update_message_status(original_msg_id, chat_id, 'PENDING', topic_id=topic_id)
        return "OFFICE_HOURS_SKIP"

    tz = pytz.timezone('Asia/Yangon')
    orig_time = datetime.fromtimestamp(original_ts, tz).strftime('%Y-%m-%d %I:%M %p')

    safe_shop = html.escape(shop_name)
    safe_text = html.escape(text)
    safe_category = html.escape(category)
    safe_intent = html.escape(intent if intent else 'အထွေထွေ')

    alert_text = (
        f"<b>{title}</b>\n"
        f"Customer စာပို့ထားသည်မှာ မိနစ် ၁၅ ပြည့်သွားပါပြီ။\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏪 ဆိုင်: <b>{safe_shop}</b>\n"
        f"💬 စာသား: {safe_text}\n"
        f"⏰ အချိန်: {orig_time}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    clean_chat_id = str(chat_id).replace("-100", "")
    msg_link = f"tg://privatepost?channel={clean_chat_id}&post={original_msg_id}"
    safe_target_topic = target_topic if target_topic != 0 else None
    
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
        telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{original_msg_id}_{chat_id}"),
        telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{original_msg_id}_{chat_id}")
    )
    
    try:
        if media_id:
            msg = _bot.send_photo(
                target_chat,
                media_id,
                caption=alert_text,
                message_thread_id=safe_target_topic,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            msg = _bot.send_message(
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
        if "message thread not found" in str(e).lower() and safe_target_topic is not None:
            log.warning(f"⚠️ Topic {safe_target_topic} not found in Central Group. Falling back to General.")
            try:
                if media_id:
                    msg = _bot.send_photo(target_chat, media_id, caption=alert_text, parse_mode="HTML", reply_markup=markup)
                else:
                    msg = _bot.send_message(target_chat, alert_text, parse_mode="HTML", reply_markup=markup)
                db_manager.save_alert_tracking(original_msg_id, chat_id, msg.message_id, target_chat)
                db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED', topic_id=topic_id, category=category, intent=intent, summary=summary)
                return msg.message_id
            except Exception as e2:
                log.error(f"❌ Final Fallback Failed: {e2}")
                return None
        
        log.error(f"❌ Failed to send alert: {e}")
        return None

def append_to_alert(target_alert_id, target_alert_chat, new_text, original_msg_id, chat_id, topic_id=None):
    try:
        safe_new_text = html.escape(new_text)
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"tg://privatepost?channel={clean_chat_id}&post={original_msg_id}"
        
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
            telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{original_msg_id}_{chat_id}"),
            telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{original_msg_id}_{chat_id}")
        )

        msg = _bot.send_message(
            target_alert_chat,
            f"➕ <b>Update for Alert:</b>\n{safe_new_text}",
            reply_to_message_id=target_alert_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
        db_manager.add_linked_msg_id(original_msg_id, chat_id, msg.message_id)
        db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED', topic_id=topic_id)
        log.info(f"➕ Appended message {original_msg_id} to alert {target_alert_id} (Topic: {topic_id})")
    except Exception as e:
        log.error(f"❌ Failed to append to alert: {e}")

def resolve_and_cleanup(msg_id, chat_id, shop_name, text, staff_name="AI/Staff", manual_resolve=False):
    is_office = is_office_hours(chat_id) or manual_resolve
    conn = db_manager.get_connection()
    msg_data = conn.execute("SELECT timestamp, topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    conn.close()
    
    duration_str = "Unknown"
    topic_id = 0
    if msg_data:
        orig_ts, topic_id = msg_data
        diff_seconds = int(time.time()) - orig_ts
        hours = diff_seconds // 3600
        minutes = (diff_seconds % 3600) // 60
        if hours > 0:
            duration_str = f"{hours}h {minutes}m"
        else:
            duration_str = f"{minutes} mins"

    tracking = db_manager.get_alert_tracking(msg_id, chat_id)
    
    if tracking:
        # alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids, esc_tier2_msg_id
        alert_msg_id, alert_chat_id, _, esc_msg_id, linked_ids_json, linked_customer_ids_json, esc_tier2_msg_id = tracking
        try:
            _bot.delete_message(alert_chat_id, alert_msg_id)
            log.info(f"🗑️ Deleted Level 1 alert {alert_msg_id}")
        except Exception as e:
            log.warning(f"⚠️ Failed to delete Level 1 alert {alert_msg_id}: {e}")
        
        if esc_msg_id:
            try:
                _bot.delete_message(MANAGER_ID, esc_msg_id)
                log.info(f"🗑️ Deleted Level 2 escalation {esc_msg_id}")
            except Exception as e:
                log.warning(f"⚠️ Failed to delete Level 2 escalation {esc_msg_id}: {e}")

        if esc_tier2_msg_id:
            try:
                _bot.delete_message(ESCALATION_GROUP_ID, esc_tier2_msg_id)
                log.info(f"🗑️ Deleted Tier 2 escalation {esc_tier2_msg_id}")
            except Exception as e:
                log.warning(f"⚠️ Failed to delete Tier 2 escalation {esc_tier2_msg_id}: {e}")
            
        if linked_ids_json:
            linked_ids = json.loads(linked_ids_json)
            for l_id in linked_ids:
                try:
                    _bot.delete_message(alert_chat_id, l_id)
                except Exception as e:
                    log.warning(f"⚠️ Failed to delete linked message {l_id}: {e}")
            log.info(f"🗑️ Deleted {len(linked_ids)} linked messages")
        
        db_manager.delete_alert_tracking(msg_id, chat_id)
    else:
        parent_id = db_manager.get_parent_msg_id(msg_id, chat_id)
        if parent_id != msg_id:
            log.info(f"🔗 Found parent {parent_id} for message {msg_id}. Retrying cleanup...")
            return resolve_and_cleanup(parent_id, chat_id, shop_name, text, staff_name, manual_resolve)
        
        log.info(f"ℹ️ No active alert tracking found for {msg_id} in {chat_id}.")

    RESOLVED_GROUP_ID = int(os.getenv('RESOLVED_GROUP_ID', -1003906164269))
    
    # Fetch pickup details if it's from Topic 1
    pickup_data = None
    if topic_id == 1:
        with db_manager.get_connection() as conn:
            pickup_data = conn.execute(
                "SELECT target_date, remark FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?",
                (msg_id, chat_id)
            ).fetchone()

    # 💡 Pick Up (Topic 1) ဖြစ်ပြီး Pickup Data ရှိပါက Topic 28 သို့ ပို့မည်၊ အခြားစာများကို Topic 3 သို့ ပို့မည်
    if topic_id == 1 and pickup_data:
        RESOLVED_TOPIC_ID = 28
    else:
        RESOLVED_TOPIC_ID = int(os.getenv('RESOLVED_TOPIC_ID', 3))

    clean_chat_id = str(chat_id).replace("-100", "")
    msg_link = f"tg://privatepost?channel={clean_chat_id}&post={msg_id}"
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))
    
    if tracking:
        try:
            if is_office:
                tz = pytz.timezone('Asia/Yangon')
                orig_time_str = datetime.fromtimestamp(orig_ts, tz).strftime('%Y-%m-%d %I:%M %p') if 'orig_ts' in locals() else datetime.now(tz).strftime('%Y-%m-%d %I:%M %p')

                if topic_id == 1 and pickup_data:
                    p_date = pickup_data[0]
                    p_remark = pickup_data[1]
                    
                    record_text = (
                        f"✅ **Pick Up Record**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🏪 ဆိုင်: {shop_name}\n"
                        f"📅 Pick Up Date: <b>{p_date}</b>\n"
                        f"📝 မှတ်ချက်: {p_remark}\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"💬 မူရင်းစာ: {text if text != '[Unknown]' else 'စာသားရှာမတွေ့ပါ'}\n"
                        f"👤 ဖြေရှင်းသူ: {staff_name}\n"
                        f"⏳ ကြာချိန်: {duration_str if duration_str != 'Unknown' else 'ချက်ချင်း'}\n"
                        f"📅 အချိန်: {orig_time_str}"
                    )
                else:
                    record_text = (
                        f"✅ **RESOLVED RECORD**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🏪 ဆိုင်: {shop_name}\n"
                        f"💬 မူရင်းစာ: {text if text != '[Unknown]' else 'စာသားရှာမတွေ့ပါ'}\n"
                        f"👤 ဖြေရှင်းသူ: {staff_name}\n"
                        f"⏳ ကြာချိန်: {duration_str if duration_str != 'Unknown' else 'ချက်ချင်း'}\n"
                        f"📅 အချိန်: {orig_time_str}"
                    )
                _bot.send_message(
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
    # 💡 Pick up (Topic 1) အတွက် Escalation Alert မပို့စေရန်
    if topic_id == 1:
        return
        
    tracking = db_manager.get_alert_tracking(msg_id, chat_id)
    if tracking:
        # alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids, esc_tier2_msg_id
        _, _, _, _, _, _, esc_tier2_msg_id = tracking
        conn = db_manager.get_connection()
        msg_data = conn.execute("SELECT timestamp FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        conn.close()
        
        if not msg_data:
            return
        
        orig_ts = msg_data[0]
        now = int(time.time())
        diff = now - orig_ts

        # 💡 30-Minute Critical SLA Alert (Tier 2 Escalation)
        if not esc_tier2_msg_id and diff >= 1800:
            try:
                tz = pytz.timezone('Asia/Yangon')
                orig_time = datetime.fromtimestamp(orig_ts, tz).strftime('%I:%M %p')
                safe_shop = html.escape(shop_name)
                safe_text = html.escape(text)

                esc_text = (
                    f"🚨 <b>30-Minute Critical SLA Alert!</b>\n"
                    f"တိုင်ကြားစာ/အော်ဒါ မိနစ် ၃၀ ပြည့်သည်အထိ မဖြေရှင်းရသေးပါ။\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏪 ဆိုင်: <b>{safe_shop}</b>\n"
                    f"💬 စာသား: {safe_text}\n"
                    f"⏰ မူရင်းအချိန်: {orig_time}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                
                clean_chat_id = str(chat_id).replace("-100", "")
                msg_link = f"tg://privatepost?channel={clean_chat_id}&post={msg_id}"
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                    telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{msg_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{msg_id}_{chat_id}")
                )

                msg = _bot.send_message(ESCALATION_GROUP_ID, esc_text, message_thread_id=ESCALATION_TOPIC_ID, reply_markup=markup, parse_mode="HTML")
                db_manager.update_alert_tracking_esc(msg_id, chat_id, msg.message_id, tier=2)
                log.warning(f"🚨 Tier 2 Escalation sent for {msg_id} to Group")
            except Exception as e:
                log.error(f"❌ Tier 2 Escalation failed: {e}")

def backup_database():
    try:
        if not os.path.exists(db_manager.DB_FILE):
            return
            
        log.info("💾 Starting Automated Database Backup...")
        with open(db_manager.DB_FILE, 'rb') as f:
            _bot.send_document(
                MANAGER_ID,
                f,
                caption=f"📅 **CarryMan DB Backup**\nအလိုအလျောက် သိမ်းဆည်းထားသော မှတ်တမ်း\nအချိန်: {datetime.now(pytz.timezone('Asia/Yangon')).strftime('%Y-%m-%d %I:%M %p')}"
            )
        log.info("✅ Database Backup sent to Manager.")
    except Exception as e:
        log.error(f"❌ Backup Error: {e}")

def send_performance_report():
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
        
        _bot.send_message(MANAGER_ID, report, parse_mode="Markdown")
        log.info("✅ Weekly Performance Report sent to Manager.")
    except Exception as e:
        log.error(f"❌ Performance Report Failed: {e}")

def send_heartbeat():
    if HEALTHCHECK_URL:
        try:
            requests.get(HEALTHCHECK_URL, timeout=10)
            log.info("💓 Heartbeat sent to HealthCheck.")
        except Exception as e:
            log.error(f"❌ Heartbeat Failed: {e}")

def process_audits(bot_instance=None):
    if bot_instance:
        set_bot(bot_instance)
    
    if not _bot:
        log.error("❌ Auditor: Bot instance not set. Exiting worker.")
        return

    log.info("🧠 Auditor (Worker 2: AI Brain) is running...")
    last_backup_date = None
    last_report_date = None
    last_heartbeat_time = 0
    last_cleanup_time = 0
    
    if not _bot:
        log.error("❌ Auditor: Bot instance not set. Worker cannot start.")
        return

    while True:
        try:
            if time.time() - last_heartbeat_time >= 300:
                send_heartbeat()
                last_heartbeat_time = time.time()
            
            if time.time() - last_cleanup_time >= 3600:
                db_manager.auto_resolve_stale_alerts(hours=30)
                last_cleanup_time = time.time()

            tz = pytz.timezone('Asia/Yangon')
            now_mm = datetime.now(tz)
            today_str = now_mm.strftime('%Y-%m-%d')
            
            if now_mm.hour == 0 and now_mm.minute < 10 and last_backup_date != today_str:
                backup_database()
                last_backup_date = today_str
                
            if now_mm.weekday() == 6 and now_mm.hour == 9 and 5 <= now_mm.minute < 15 and last_report_date != today_str:
                send_performance_report()
                last_report_date = today_str

            # Phase 2: AI Gatekeeper Logic for Auditor
            SANDBOX_CHAT_ID = -1003539520778
            # SLA Alerts & Group Auditing should follow Office Hours (6:15 PM limit)
            is_office = is_office_hours()
            global_ai = db_manager.get_ai_global_status()
            global_alert = db_manager.get_alert_system_global_status()
            
            pending_topics = db_manager.get_pending_topics(minutes=15)
            
            filtered_topics = []
            for c_id, t_id in pending_topics:
                # Sandbox/Test Group always allowed
                if c_id == TEST_GROUP_ID or c_id == SANDBOX_CHAT_ID:
                    log.info(f"🧪 Sandbox Audit: Bypassing restrictions for chat {c_id}")
                    filtered_topics.append((c_id, t_id))
                    continue
                
                # Global & Group & Time Check
                if global_alert == 'ON' and is_office:
                    filtered_topics.append((c_id, t_id))
                else:
                    # If AI is off, we don't audit, but we might still need to handle alerts manually?
                    # For now, we just skip AI auditing if conditions aren't met.
                    pass
            pending_topics = filtered_topics

            for chat_id, topic_id in pending_topics:
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                msgs = db_manager.get_pending_messages(minutes=15, limit=20, chat_id=chat_id, topic_id=topic_id, all_pending=True)
                if not msgs: continue

                log.info(f"📂 Auditing Batch: {shop_name} (Topic {topic_id}) in Chat {chat_id} ({len(msgs)} messages)")
                trigger_msg_id, _, _, trigger_text, trigger_ts, trigger_media_id = msgs[0]
                active_alerts = db_manager.get_active_alerts_for_group(chat_id, topic_id)
                preceding_msgs = db_manager.get_messages_before(chat_id, topic_id, trigger_msg_id)
                subsequent_msgs = db_manager.get_messages_after(chat_id, topic_id, msgs[-1][0])

                safe_chat_id = int(chat_id)
                safe_topic_id = int(topic_id)
                
                for m in msgs:
                    db_manager.update_message_status(int(m[0]), safe_chat_id, 'AUDITING', topic_id=safe_topic_id)
                
                action, ai_res = evaluate_with_ai(shop_name, msgs, active_alerts, preceding_msgs, subsequent_msgs, safe_chat_id, safe_topic_id)
                grouped_ids = ai_res.get("grouped_msg_ids", [trigger_msg_id])
                if trigger_msg_id not in grouped_ids: grouped_ids.append(trigger_msg_id)

                if action == "RESOLVE":
                    for mid in grouped_ids:
                        db_manager.update_message_status(mid, chat_id, 'HANDLED_BY_AI', topic_id=topic_id)
                    resolve_and_cleanup(trigger_msg_id, chat_id, shop_name, trigger_text, "AI Auto-Resolve")
                    log.info(f"✨ AI Auto-Resolved Group: {grouped_ids}")

                elif action == "IGNORE":
                    for mid in grouped_ids:
                        db_manager.update_message_status(mid, chat_id, 'HANDLED_BY_AI', topic_id=topic_id)
                    log.info(f"🔇 AI Ignored Group: {grouped_ids}")

                elif action == "APPEND" and ai_res.get("target_alert_id"):
                    target_chat, target_topic = get_routing_data(chat_id, topic_id, category=ai_res.get("category", ""), intent=ai_res.get("intent", ""))
                    if target_chat and target_topic:
                        combined_text = "\n".join([m[3] for m in msgs if m[0] in grouped_ids])
                        append_to_alert(ai_res["target_alert_id"], target_chat, combined_text, trigger_msg_id, chat_id, topic_id=topic_id)
                        for mid in grouped_ids:
                            if mid != trigger_msg_id:
                                db_manager.add_linked_customer_id(trigger_msg_id, chat_id, mid)
                                db_manager.update_message_status(mid, chat_id, 'ALERTED', topic_id=topic_id, category=ai_res.get("category"), intent=ai_res.get("intent"))
                    else:
                        for mid in msgs: db_manager.update_message_status(mid, chat_id, 'PENDING', topic_id=topic_id)

                elif action == "NEW_ALERT":
                    combined_text = "\n".join([m[3] for m in msgs if m[0] in grouped_ids])
                    alert_id = send_new_alert(
                        chat_id, topic_id, trigger_msg_id, combined_text,
                        ai_res.get("summary", "New Grouped Issue"),
                        shop_name, trigger_ts,
                        category=ai_res.get("category", "အခြား"),
                        intent=ai_res.get("intent", "အထွေထွေစုံစမ်းမှု"),
                        media_id=trigger_media_id
                    )
                    if alert_id:
                        for mid in grouped_ids:
                            if mid != trigger_msg_id:
                                db_manager.add_linked_customer_id(trigger_msg_id, chat_id, mid)
                                db_manager.update_message_status(mid, chat_id, 'ALERTED', topic_id=topic_id)
                    else:
                        for m in msgs:
                            mid = m[0]
                            if mid != trigger_msg_id:
                                db_manager.update_message_status(mid, chat_id, 'WAITING_ROUTE', topic_id=topic_id)

                for m in msgs:
                    mid = m[0]
                    if mid not in grouped_ids:
                        db_manager.update_message_status(mid, chat_id, 'PENDING', topic_id=topic_id)

                if action not in ["RESOLVE", "IGNORE", "APPEND", "NEW_ALERT"]:
                    for m in msgs: db_manager.update_message_status(m[0], chat_id, 'PENDING', topic_id=topic_id)
                
                time.sleep(2)

            conn = db_manager.get_connection()
            alerted_msgs = conn.execute(
                "SELECT msg_id, chat_id, topic_id, text FROM message_logs WHERE status = 'ALERTED' AND status != 'HANDLED_BY_AI'"
            ).fetchall()
            conn.close()
            
            for m_id, c_id, t_id, txt in alerted_msgs:
                # Sandbox bypass for escalation
                is_sandbox = (c_id == TEST_GROUP_ID or c_id == SANDBOX_CHAT_ID)
                
                # Escalations also follow Office Hours
                if not is_office and not is_sandbox:
                    continue
                
                if global_alert != 'ON' and not is_sandbox:
                    continue

                _, _, s_name = db_manager.get_topic_context(c_id, t_id)
                handle_escalation(m_id, c_id, s_name, txt, t_id)

            time.sleep(30)

        except Exception as e:
            context_parts = []
            if 'shop_name' in locals(): context_parts.append(f"Shop: {shop_name}")
            if 'chat_id' in locals(): context_parts.append(f"Chat: {chat_id}")
            if 'topic_id' in locals(): context_parts.append(f"Topic: {topic_id}")
            context = f" ({', '.join(context_parts)})" if context_parts else ""
            log.error(f"⚠️ Auditor Loop Error{context}: {e}")
            time.sleep(10)

def run(data, event):
    """
    Standard Module Entry Point
    data: dict (optional payload)
    event: str (e.g., "start_worker")
    """
    if event == "start_worker":
        process_audits(data.get("bot"))
        return True, "Auditor worker finished"
    
    return False, f"Unknown event: {event}"

def handle(bot, message):
    """
    Central Router မှတစ်ဆင့် ခေါ်ယူသည့် Entry Point
    """
    global _bot
    if not _bot: _bot = bot
    log.info(f"🛡️ Auditor module handled message: {message.message_id}")
    # Auditor သည် Worker အနေဖြင့် သီးသန့် Run နေသော်လည်း Router မှ ခေါ်ယူနိုင်ရန် handle() ထည့်ထားခြင်းဖြစ်သည်

if __name__ == "__main__":
    run(data={}, event="start_worker")
