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
        subsequent_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(s[1]) else 'Customer'} ({s[3]}): {s[0]}" for s in subsequent_msgs]) if subsequent_msgs else "None"

        prompt = f"""
        Role: Senior Auditor for "{group_name}" Delivery Service.
        Task: Evaluate if a pending message needs a NEW alert, can be APPENDED to an existing alert, or is already RESOLVED.

        [Target Message]: "{target_msg}"
        
        [Active Alerts in this Group]:
        {active_context}

        [Subsequent Messages (After Target)]:
        {subsequent_context}

        Rules:
        1. RESOLVE: If subsequent messages (especially from Staff or if status is RESOLVED) show the issue is handled.
        2. APPEND: If the target message is about the same topic/order as an active alert.
        3. NEW_ALERT: If it's a new issue that hasn't been alerted yet.

        Output ONLY JSON:
        {{
            "action": "NEW_ALERT" | "APPEND" | "RESOLVE",
            "target_alert_id": alert_msg_id (if APPEND, else null),
            "summary": "Short Burmese summary (3-5 words) if NEW_ALERT",
            "category": "ငွေလွှဲ / ပစ္စည်းစုံစမ်း / လိပ်စာပြင် / အခြား" (Choose one if NEW_ALERT)
        }}
        """

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

def get_routing_data(chat_id, topic_id):
    """
    ဗဟိုမှ ထိန်းချုပ်ခြင်း (Central Mapping)
    OS Group ၏ Topic အလိုက် သတ်မှတ်ထားသော Central Alert Group ဆီသို့ လမ်းကြောင်းပေးခြင်း
    """
    CENTRAL_GROUP_ID = -1003601049225
    
    # ၁။ Database မှ Topic Name ကို ရှာမည်
    conn = db_manager.get_connection()
    
    # Topic ID 0 (General) ဖြစ်ပါက ထို Group ထဲရှိ အခြား Topic များကို အရင်စစ်မည်
    if topic_id == 0:
        # အကယ်၍ Group ထဲမှာ Pick Up သို့မဟုတ် General ဆိုတဲ့ Topic ရှိနေရင် အဲ့ဒါကို သုံးမည်
        res = conn.execute(
            "SELECT topic_name FROM os_groups WHERE chat_id = ? AND (topic_name LIKE '%Pick Up%' OR topic_name LIKE '%General%') LIMIT 1",
            (chat_id,)
        ).fetchone()
        if res:
            topic_name = res[0]
        else:
            # ဘာမှမရှိပါက Default အနေဖြင့် Pick Up ဆီသို့ ပို့မည်
            return CENTRAL_GROUP_ID, 1
    else:
        res = conn.execute(
            "SELECT topic_name FROM os_groups WHERE chat_id = ? AND topic_id = ?",
            (chat_id, topic_id)
        ).fetchone()
        if not res:
            return MANAGER_ID, 0
        topic_name = res[0]
        
    conn.close()
    
    # ၂။ Mapping Table အတိုင်း Topic ID သတ်မှတ်ခြင်း
    if "Error" in topic_name:
        return CENTRAL_GROUP_ID, 37
    elif "Pick Up" in topic_name or "စုံစမ်းရန်" in topic_name or "General" in topic_name:
        return CENTRAL_GROUP_ID, 1
    elif "Fin" in topic_name or "Voc" in topic_name:
        return CENTRAL_GROUP_ID, 35
        
    # သတ်မှတ်မထားသော Topic များအတွက် Central Pick Up ဆီသို့ ပို့မည် (Catch-all)
    return CENTRAL_GROUP_ID, 1

def send_new_alert(chat_id, topic_id, original_msg_id, text, summary, shop_name, original_ts, category="အခြား"):
    """ ဝန်ထမ်း group ထံသို့ Alert အသစ်ပို့ခြင်း (View Message & Done Buttons ပါဝင်သည်) """
    target_chat, target_topic = get_routing_data(chat_id, topic_id)
    
    # အချိန်ကို မြန်မာစံတော်ချိန်ဖြင့် ပြောင်းလဲခြင်း
    tz = pytz.timezone('Asia/Yangon')
    orig_time = datetime.fromtimestamp(original_ts, tz).strftime('%I:%M %p')

    alert_text = (
        f"⚠️ **Pending Alert (15 Mins)**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏪 ဆိုင်: **{shop_name}**\n"
        f"📂 အမျိုးအစား: #{category}\n"
        f"� အကြောင်းအရာ: {summary}\n"
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
        # လက်ရှိ message ကိုယူရန် (Telegram API ကနေ တိုက်ရိုက်ယူလို့မရလို့ logic အရ update ပဲလုပ်မယ်)
        # တကယ်တမ်းက alert text ကို db မှာသိမ်းထားရင် ပိုကောင်းမယ်။
        # လောလောဆယ် alert message အောက်မှာ reply ပြန်တဲ့ပုံစံနဲ့သွားမယ် (သို့) edit လုပ်မယ်။
        # Edit လုပ်ဖို့က မူရင်းစာသားလိုတယ်။
        
        bot.send_message(
            target_alert_chat,
            f"➕ **Update for Alert:**\n{new_text}",
            reply_to_message_id=target_alert_id
        )
        db_manager.update_message_status(original_msg_id, chat_id, 'ALERTED')
        log.info(f"➕ Appended message {original_msg_id} to alert {target_alert_id}")
    except Exception as e:
        log.error(f"❌ Failed to append to alert: {e}")

def resolve_and_cleanup(msg_id, chat_id, shop_name, text, staff_name="AI/Staff"):
    """ Alert ကိုဖျက်ပြီး Record Group သို့ ပို့ခြင်း (View Message Button ပါဝင်သည်) """
    # ၁။ မူရင်းစာ ဝင်ခဲ့သည့် အချိန်ကို ရှာခြင်း
    conn = db_manager.get_connection()
    msg_data = conn.execute("SELECT timestamp FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    conn.close()
    
    duration_str = "Unknown"
    if msg_data:
        orig_ts = msg_data[0]
        diff_seconds = int(time.time()) - orig_ts
        
        # ကြာချိန်ကို နာရီ/မိနစ်ဖြင့် တွက်ချက်ခြင်း
        hours = diff_seconds // 3600
        minutes = (diff_seconds % 3600) // 60
        if hours > 0:
            duration_str = f"{hours}h {minutes}m"
        else:
            duration_str = f"{minutes} mins"

    # ၂။ Alert Message ကို ဖျက်ခြင်း
    tracking = db_manager.get_alert_tracking(msg_id, chat_id)
    if tracking:
        alert_msg_id, alert_chat_id, _ = tracking
        try:
            bot.delete_message(alert_chat_id, alert_msg_id)
            log.info(f"🗑️ Deleted alert {alert_msg_id}")
        except Exception as e:
            log.warning(f"⚠️ Could not delete alert {alert_msg_id}: {e}")
        
        db_manager.delete_alert_tracking(msg_id, chat_id)

    # ၃။ Record Group သို့ ပို့ခြင်း (Central Group: -1003601049225, Topic: 4)
    RESOLVED_GROUP_ID = -1003601049225
    RESOLVED_TOPIC_ID = 4
    
    # [ 🔗 View Message ] Button တည်ဆောက်ခြင်း
    clean_chat_id = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{clean_chat_id}/{msg_id}"
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))
    
    try:
        record_text = (
            f"✅ **RESOLVED RECORD**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: {shop_name}\n"
            f"💬 မူရင်းစာ: {text}\n"
            f"👤 ဖြေရှင်းသူ: {staff_name}\n"
            f"⏳ ကြာချိန်: {duration_str}\n"
            f"📅 အချိန်: {datetime.now(pytz.timezone('Asia/Yangon')).strftime('%Y-%m-%d %I:%M %p')}"
        )
        bot.send_message(
            RESOLVED_GROUP_ID,
            record_text,
            message_thread_id=RESOLVED_TOPIC_ID,
            reply_markup=markup
        )
    except Exception as e:
        log.error(f"❌ Failed to log to record group: {e}")

def handle_escalation(msg_id, chat_id, shop_name, text, topic_id):
    """ ၃၀ မိနစ်ပြည့်ပါက Manager ထံ Escalation ပို့ခြင်း """
    tracking = db_manager.get_alert_tracking(msg_id, chat_id)
    if tracking:
        _, _, created_at = tracking
        if int(time.time()) - created_at >= 1800: # 30 mins
            try:
                esc_text = (
                    f"🚨 **LEVEL 2 ESCALATION (30 Mins)**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏪 ဆိုင်: **{shop_name}**\n"
                    f"⚠️ ဖြေရှင်းခြင်းမရှိသေးသောစာ: {text}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Manager အနေဖြင့် စစ်ဆေးပေးပါရန်။"
                )
                bot.send_message(MANAGER_ID, esc_text)
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
            
            for msg_id, chat_id, topic_id, text, ts, media_id in pending_msgs:
                active_alerts = db_manager.get_active_alerts_for_group(chat_id, topic_id)
                subsequent_msgs = db_manager.get_messages_after(chat_id, topic_id, msg_id)
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                # 💡 Media Support: အကယ်၍ လက်ရှိစာမှာ ပုံမပါရင် အပေါ်က ကပ်ရပ်စာမှာ ပုံပါ၊ မပါ စစ်မည်
                if not media_id:
                    conn = db_manager.get_connection()
                    # ထို user ပို့ထားသော လွန်ခဲ့သည့် ၅ မိနစ်အတွင်းက နောက်ဆုံးပုံကို ရှာမည်
                    prev_media = conn.execute(
                        "SELECT media_id FROM message_logs WHERE chat_id=? AND media_id IS NOT NULL AND timestamp >= ? ORDER BY timestamp DESC LIMIT 1",
                        (chat_id, ts - 300)
                    ).fetchone()
                    conn.close()
                    if prev_media:
                        media_id = prev_media[0]

                # Duplicate Alert ကာကွယ်ရန် Status ကို ခေတ္တပြောင်းထားမည်
                db_manager.update_message_status(msg_id, chat_id, 'AUDITING')
                
                action, ai_res = evaluate_with_ai(shop_name, text, active_alerts, subsequent_msgs)
                
                if action == "RESOLVE":
                    db_manager.update_message_status(msg_id, chat_id, 'RESOLVED')
                    resolve_and_cleanup(msg_id, chat_id, shop_name, text, "AI Auto-Resolve")
                    log.info(f"✨ AI Auto-Resolved: {msg_id} in {shop_name}")
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
            # ဒီအပိုင်းအတွက် db_manager မှာ function အသစ်လိုနိုင်တယ် ဒါမှမဟုတ် query တိုက်ရိုက်ရေးမယ်
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
