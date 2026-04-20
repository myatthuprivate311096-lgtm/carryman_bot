# Version: 2.0 (Worker 2: AI Brain - Standalone Auditor)
import os
import time
import json
import telebot
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import log
import db_manager
from openai import OpenAI
import requests

# 💡 Absolute Path Fix
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID', 0))
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY')
RECORD_GROUP_ID = int(os.getenv('ARCHIVE_CHAT_ID', 0))
HEALTHCHECK_URL = os.getenv('HEALTHCHECK_URL')

bot = telebot.TeleBot(BOT_TOKEN)

def is_office_hours():
    """
    Asia/Yangon Timezone (UTC+6:30)
    Business Hours: 09:00 AM - 06:00 PM
    Grace Period: Start at 09:10 AM
    """
    tz = pytz.timezone('Asia/Yangon')
    mm_time = datetime.now(tz)
    
    current_hour = mm_time.hour
    current_minute = mm_time.minute
    
    # 09:10 AM to 05:59 PM
    if (current_hour == 9 and current_minute >= 10) or (9 < current_hour < 18):
        return True
    return False

def evaluate_with_ai(group_name, target_msg, active_alerts, subsequent_msgs):
    """ Gemini API ကိုသုံး၍ Message ၏ အခြေအနေကို ဆုံးဖြတ်ခြင်း """
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
        
        # 💡 Filter out existing alert messages from subsequent messages to prevent loop
        active_alert_ids = [a[0] for a in active_alerts]
        filtered_subsequent = [s for s in subsequent_msgs if s[0] not in [a[2] for a in active_alerts]]
        
        subsequent_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(s[1]) else 'Customer'} ({s[3]}): {s[0]}" for s in filtered_subsequent]) if filtered_subsequent else "None"

        prompt = f"""
        Role: Senior Auditor for "{group_name}" Delivery Service.
        Task: Evaluate if a pending message needs a NEW alert, can be APPENDED to an existing alert, is already RESOLVED, or should be IGNORED.

        [Target Message]: "{target_msg}"
        
        [Active Alerts in this Group]:
        {active_context}

        [Subsequent Messages (After Target)]:
        {subsequent_context}

        Rules:
        1. IGNORE: If the target message is just a "thank you", "ok", "hello", or irrelevant noise (e.g., "ကျေးဇူးပါ", "ဟုကဲ့", "hi").
        2. RESOLVE: If subsequent messages (especially from Staff or if status is RESOLVED) show the issue is handled.
        3. APPEND: If the target message is about the same topic/order as an active alert.
        4. NEW_ALERT: If it's a new issue that hasn't been alerted yet.

        Output ONLY JSON:
        {{
            "action": "NEW_ALERT" | "APPEND" | "RESOLVE" | "IGNORE",
            "target_alert_id": alert_msg_id (if APPEND, else null),
            "summary": "Short Burmese summary (3-5 words) if NEW_ALERT",
            "category": "ငွေလွှဲ / ပစ္စည်းစုံစမ်း / လိပ်စာပြင် / အခြား" (Choose one if NEW_ALERT)
        }}
        """

        # Use a more stable model name if needed, but keeping original for now
        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=20.0
        )
        
        res_data = json.loads(response.choices[0].message.content.strip())
        action = res_data.get("action", "NEW_ALERT")
        # AI က RESOLVE လို့ပြောပေမယ့် staff message မရှိရင် NEW_ALERT အဖြစ်ထားမယ် (Safety Check)
        if action == "RESOLVE" and not subsequent_msgs:
            action = "NEW_ALERT"
        return action, res_data
    except Exception as e:
        log.error(f"❌ Auditor AI Error: {e}")
        return "ERROR", {"summary": "AI Error - Retrying..."}

def get_target_routing(topic_id):
    """
    Central Mapping Table မှ Topic ID အလိုက် Target Group/Topic ကို ရှာဖွေခြင်း။
    """
    conn = db_manager.get_connection()
    res = conn.execute(
        "SELECT target_group_id, target_topic_id FROM routing_table WHERE topic_id = ?",
        (topic_id,)
    ).fetchone()
    conn.close()
    
    if res:
        return res[0], res[1]
    
    # Default fallback
    return -1003601049225, 0

def get_routing_data(chat_id, topic_id):
    """
    ဗဟိုမှ ထိန်းချုပ်ခြင်း (Central Mapping)
    OS Group ၏ Topic အလိုက် သတ်မှတ်ထားသော Central Alert Group ဆီသို့ လမ်းကြောင်းပေးခြင်း
    """
    # ၁။ Database မှ Topic Name ကို ရှာမည်
    conn = db_manager.get_connection()
    
    # Topic ID 0 (General) ဖြစ်ပါက ထို Group ထဲရှိ အခြား Topic များကို အရင်စစ်မည်
    if topic_id == 0:
        # အကယ်၍ Group ထဲမှာ Pick Up သို့မဟုတ် General ဆိုတဲ့ Topic ရှိနေရင် အဲ့ဒါကို သုံးမည်
        res = conn.execute(
            "SELECT topic_name, topic_id FROM os_groups WHERE chat_id = ? AND (topic_name LIKE '%Pick Up%' OR topic_name LIKE '%General%') LIMIT 1",
            (chat_id,)
        ).fetchone()
        if res:
            topic_name, target_topic = res
        else:
            # ဘာမှမရှိပါက Default အနေဖြင့် CENTRAL_GROUP_ID, topic=0 (general chat)
            log.info(f"📡 Routing: chat={chat_id}, topic=0 -> CENTRAL_GROUP_ID, topic=0 (default)")
            return -1003601049225, 0
    else:
        res = conn.execute(
            "SELECT topic_name, topic_id FROM os_groups WHERE chat_id = ? AND topic_id = ?",
            (chat_id, topic_id)
        ).fetchone()
        if not res:
            log.warning(f"⚠️ Routing: No topic found for chat={chat_id}, topic={topic_id} -> MANAGER_ID")
            return MANAGER_ID, 0
        topic_name, target_topic = res
        
    conn.close()
    
    # ၂။ Mapping Table အတိုင်း Topic ID သတ်မှတ်ခြင်း
    target_group, target_topic = get_target_routing(target_topic)
    
    log.info(f"📡 Routing: chat={chat_id}, topic_name='{topic_name}' -> Group={target_group}, Topic={target_topic}")
    return target_group, target_topic

def send_new_alert(chat_id, topic_id, original_msg_id, text, summary, shop_name, original_ts, category="အခြား", media_id=None):
    """ ဝန်ထမ်း group ထံသို့ Alert အသစ်ပို့ခြင်း (View Message & Done Buttons ပါဝင်သည်) """
    target_chat, target_topic = get_routing_data(chat_id, topic_id)
    
    # အချိန်ကို မြန်မာစံတော်ချိန်ဖြင့် ပြောင်းလဲခြင်း
    tz = pytz.timezone('Asia/Yangon')
    orig_time = datetime.fromtimestamp(original_ts, tz).strftime('%Y-%m-%d %I:%M %p')

    alert_text = (
        f"⚠️ **Pending Alert (15 Mins)**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏪 ဆိုင်: **{shop_name}**\n"
        f" အကြောင်းအရာ: {summary}\n"
        f"💬 စာသား: {text}\n"
        f"⏰ အချိန်: {orig_time}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    # Buttons တည်ဆောက်ခြင်း
    clean_chat_id = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{clean_chat_id}/{original_msg_id}"
    
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
        telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{original_msg_id}_{chat_id}")
    )
    
    try:
        if media_id:
            msg = bot.send_photo(
                target_chat,
                media_id,
                caption=alert_text,
                message_thread_id=target_topic,
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            msg = bot.send_message(
                target_chat,
                alert_text,
                message_thread_id=target_topic,
                parse_mode="Markdown",
                reply_markup=markup
            )
        db_manager.save_alert_tracking(original_msg_id, chat_id, msg.message_id, target_chat)
        db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED')
        log.info(f"📢 Alert sent for {original_msg_id} in {shop_name} to Central Group")
        return msg.message_id
    except Exception as e:
        log.error(f"❌ Failed to send alert: {e}")
        return None

def append_to_alert(target_alert_id, target_alert_chat, new_text, original_msg_id, chat_id):
    """ ရှိပြီးသား Alert ထဲသို့ စာသွားပေါင်းခြင်း """
    try:
        msg = bot.send_message(
            target_alert_chat,
            f"➕ **Update for Alert:**\n{new_text}",
            reply_to_message_id=target_alert_id
        )
        # 💡 Linked Message ID ကို သိမ်းဆည်းခြင်း (နောက်မှ ပြန်ဖျက်နိုင်ရန်)
        db_manager.add_linked_msg_id(original_msg_id, chat_id, msg.message_id)
        db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED')
        log.info(f"➕ Appended message {original_msg_id} to alert {target_alert_id}")
    except Exception as e:
        log.error(f"❌ Failed to append to alert: {e}")

def resolve_and_cleanup(msg_id, chat_id, shop_name, text, staff_name="AI/Staff"):
    """ Alert ကိုဖျက်ပြီး Record Group သို့ ပို့ခြင်း (View Message Button ပါဝင်သည်) """
    # 💡 အလုပ်ချိန်ပြင်ပဖြစ်ပါက Record မထုတ်ရန် (အစ်ကို့တောင်းဆိုချက်အရ)
    # သို့သော် Alert Cleanup ကိုတော့ ဆက်လုပ်ပေးရမည်
    is_office = is_office_hours()

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
        alert_msg_id, alert_chat_id, _, esc_msg_id, linked_ids_json = tracking
        
        # (က) မူရင်း Alert ကို ဖျက်ခြင်း
        try:
            bot.delete_message(alert_chat_id, alert_msg_id)
            log.info(f"🗑️ Deleted alert {alert_msg_id}")
        except Exception as e:
            log.warning(f"⚠️ Failed to delete alert message {alert_msg_id}: {e}")
        
        # (ခ) Manager ဆီက Escalation စာကို ဖျက်ခြင်း
        if esc_msg_id:
            try:
                bot.delete_message(MANAGER_ID, esc_msg_id)
                log.info(f"🗑️ Deleted escalation message {esc_msg_id}")
            except Exception as e:
                log.warning(f"⚠️ Failed to delete escalation message {esc_msg_id}: {e}")
            
        # (ဂ) စာသွားပေါင်းထားသော (Append) စာများကို ဖျက်ခြင်း
        if linked_ids_json:
            import json
            linked_ids = json.loads(linked_ids_json)
            for l_id in linked_ids:
                try:
                    bot.delete_message(alert_chat_id, l_id)
                except Exception as e:
                    log.warning(f"⚠️ Failed to delete linked message {l_id}: {e}")
            log.info(f"🗑️ Deleted {len(linked_ids)} linked messages")
        
        db_manager.delete_alert_tracking(msg_id, chat_id)
    else:
        # 💡 Fix: Tracking မရှိရင်တောင် Alert message တွေ ရှိနေနိုင်သေးလို့
        # အကယ်၍ Alert တက်ထားတဲ့ status ဖြစ်နေရင် tracking ကို ပြန်ရှာပြီး ဖျက်ဖို့ ကြိုးစားမယ်
        log.info(f"ℹ️ No active alert tracking found for {msg_id} in {chat_id}. Checking if it was already alerted.")

    # ၃။ Record Group သို့ ပို့ခြင်း (Alert တက်ခဲ့သော စာများကိုသာ Record ပို့မည်)
    # 💡 Fix: Resolved Record ကို သတ်မှတ်ထားသော Resolved Group သို့ ပို့ရန်
    # လက်ရှိ get_routing_data က Alert Group ကိုပဲ ပြန်ပေးနေတာကြောင့်
    # သီးသန့် Resolved Group ID ကို သုံးရပါမယ်။
    
    # အစ်ကို့တောင်းဆိုချက်အရ Resolved Group ID ကို သီးသန့်သတ်မှတ်ပါမည်
    RESOLVED_GROUP_ID = -1003601049225 # အစ်ကိုပြောတဲ့ Group ID
    RESOLVED_TOPIC_ID = 4 # အစ်কেပြောတဲ့ Topic ID
    
    # [ 🔗 View Message ] Button တည်ဆောက်ခြင်း
    clean_chat_id = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{clean_chat_id}/{msg_id}"
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))
    
    # 💡 Strict Check: Alert တက်ခဲ့မှသာ Record ပို့မည်
    if tracking:
        try:
            if is_office:
                record_text = (
                    f"✅ **RESOLVED RECORD**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏪 ဆိုင်: {shop_name}\n"
                    f"💬 မူရင်းစာ: {text if text != '[Unknown]' else 'စာသားရှာမတွေ့ပါ'}\n"
                    f"👤 ဖြေရှင်းသူ: {staff_name}\n"
                    f"⏳ ကြာချိန်: {duration_str if duration_str != 'Unknown' else 'ချက်ချင်း'}\n"
                    f"📅 အချိန်: {datetime.now(pytz.timezone('Asia/Yangon')).strftime('%Y-%m-%d %I:%M %p')}"
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
        _, _, created_at, esc_msg_id, _ = tracking
        
        # မူရင်းအချိန်ကို ရှာခြင်း
        conn = db_manager.get_connection()
        msg_data = conn.execute("SELECT timestamp FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        conn.close()
        
        if not msg_data:
            return
        
        orig_ts = msg_data[0]
        
        # Escalation မပို့ရသေးလျှင် သို့မဟုတ် ၃၀ မိနစ်ကျော်နေလျှင်
        if not esc_msg_id and (int(time.time()) - orig_ts >= 1800):
            try:
                tz = pytz.timezone('Asia/Yangon')
                orig_time = datetime.fromtimestamp(orig_ts, tz).strftime('%I:%M %p')

                esc_text = (
                    f"🚨 **LEVEL 2 ESCALATION (30 Mins)**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏪 ဆိုင်: **{shop_name}**\n"
                    f"⚠️ ဖြေရှင်းခြင်းမရှိသေးသောစာ: {text}\n"
                    f"⏰ မူရင်းအချိန်: {orig_time}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Manager အနေဖြင့် စစ်ဆေးပေးပါရန်။"
                )
                
                # Buttons တည်ဆောက်ခြင်း
                clean_chat_id = str(chat_id).replace("-100", "")
                msg_link = f"https://t.me/c/{clean_chat_id}/{msg_id}"
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                    telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{msg_id}_{chat_id}")
                )

                msg = bot.send_message(MANAGER_ID, esc_text, reply_markup=markup, parse_mode="Markdown")
                db_manager.update_alert_tracking_esc(msg_id, chat_id, msg.message_id)
                db_manager.update_message_status(msg_id, chat_id, 'ESCALATED')
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
    """ Main Auditor Loop """
    log.info("🧠 Auditor (Worker 2: AI Brain) is running...")
    last_backup_date = None
    last_report_date = None
    last_heartbeat_time = 0
    
    while True:
        try:
            # 💡 ၀။ Health Monitoring (၅ မိနစ်တစ်ကြိမ် Heartbeat ပို့မည်)
            if time.time() - last_heartbeat_time >= 300:
                send_heartbeat()
                last_heartbeat_time = time.time()

            # 💡 ၁။ Automated Backup & Analytics Report
            tz = pytz.timezone('Asia/Yangon')
            now_mm = datetime.now(tz)
            today_str = now_mm.strftime('%Y-%m-%d')
            
            # နေ့စဉ် မနက် ၉:၀၀ တွင် Backup လုပ်မည်
            if now_mm.hour == 9 and now_mm.minute < 10 and last_backup_date != today_str:
                backup_database()
                last_backup_date = today_str
                
            # အပတ်စဉ် တနင်္ဂနွေနေ့ မနက် ၉:၀၅ တွင် Performance Report ပို့မည်
            if now_mm.weekday() == 6 and now_mm.hour == 9 and 5 <= now_mm.minute < 15 and last_report_date != today_str:
                send_performance_report()
                last_report_date = today_str

            if not is_office_hours():
                time.sleep(60)
                continue

            # ၁။ ၁၅ မိနစ်ကျော်နေသော Pending စာများကို ရှာမည် (Safe Recovery: တစ်ခါလျှင် ၅ စောင်စီသာ)
            pending_msgs = db_manager.get_pending_messages(minutes=15, limit=5)
            
            for msg_id, chat_id, topic_id, text, ts in pending_msgs:
                # 💡 Logging added to track audit process
                log.info(f"🔍 Auditing message {msg_id} in chat {chat_id} (Topic: {topic_id})")
                active_alerts = db_manager.get_active_alerts_for_group(chat_id, topic_id)
                subsequent_msgs = db_manager.get_messages_after(chat_id, topic_id, msg_id)
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                # Media Support: Temporarily disabled due to missing column
                media_id = None

                # Duplicate Alert ကာကွယ်ရန် Status ကို ခေတ္တပြောင်းထားမည်
                db_manager.update_message_status(msg_id, chat_id, 'AUDITING')
                
                action, ai_res = evaluate_with_ai(shop_name, text, active_alerts, subsequent_msgs)
                
                if action == "RESOLVE":
                    db_manager.update_message_status(msg_id, chat_id, 'RESOLVED')
                    resolve_and_cleanup(msg_id, chat_id, shop_name, text, "AI Auto-Resolve")
                    log.info(f"✨ AI Auto-Resolved: {msg_id} in {shop_name}")
                elif action == "IGNORE":
                    db_manager.update_message_status(msg_id, chat_id, 'RESOLVED')
                    log.info(f"🔇 AI Ignored (Irrelevant): {msg_id} in {shop_name}")
                elif action == "APPEND" and ai_res.get("target_alert_id"):
                    # Alert ID ကနေ chat_id ရှာဖို့လိုတယ် (လောလောဆယ် routing ကနေပဲယူမယ်)
                    target_chat, _ = get_routing_data(chat_id, topic_id)
                    append_to_alert(ai_res["target_alert_id"], target_chat, text, msg_id, chat_id)
                elif action == "NEW_ALERT":
                    send_new_alert(
                        chat_id, topic_id, msg_id, text,
                        ai_res.get("summary", "New Issue"),
                        shop_name, ts,
                        ai_res.get("category", "အခြား"),
                        media_id
                    )
                else:
                    # If error or unknown, keep as PENDING
                    db_manager.update_message_status(msg_id, chat_id, 'PENDING')
                
                # Safe Recovery: Rate limit ကာကွယ်ရန် ခေတ္တနားမည်
                time.sleep(2)

            # ၂။ Escalation Check (ALERTED ဖြစ်နေတာ နာရီဝက်ကျော်ရင်)
            conn = db_manager.get_connection()
            alerted_msgs = conn.execute(
                "SELECT msg_id, chat_id, topic_id, text FROM message_logs WHERE status = 'ALERTED'"
            ).fetchall()
            conn.close()
            
            for m_id, c_id, t_id, txt in alerted_msgs:
                _, _, s_name = db_manager.get_topic_context(c_id, t_id)
                handle_escalation(m_id, c_id, s_name, txt, t_id)

            time.sleep(30)

        except Exception as e:
            log.error(f"⚠️ Auditor Loop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    process_audits()
