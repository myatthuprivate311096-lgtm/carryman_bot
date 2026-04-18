# Version: 2.0 (Worker 2: AI Brain - Standalone Auditor)
import os
import time
import json
import telebot
from datetime import datetime, timedelta
from dotenv import load_dotenv
from logger import log
import db_manager

# 💡 Absolute Path Fix
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MANAGER_ID = int(os.getenv('MANAGER_ID', 0))
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY')
RECORD_GROUP_ID = int(os.getenv('ARCHIVE_CHAT_ID', 0))

bot = telebot.TeleBot(BOT_TOKEN)

def is_office_hours():
    """ 
    Asia/Yangon Timezone (UTC+6:30) 
    Business Hours: 09:00 AM - 06:00 PM
    Grace Period: Start at 09:10 AM
    """
    now_utc = datetime.utcnow()
    mm_time = now_utc + timedelta(hours=6, minutes=30)
    
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
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1", 
            api_key=GEMINI_API_KEY
        )

        # Context တည်ဆောက်ခြင်း
        active_context = "\n".join([f"- AlertID: {a[0]} | Content: {a[2]}" for a in active_alerts]) if active_alerts else "None"
        subsequent_context = "\n".join([f"- {'Staff' if db_manager.check_if_staff(s[1]) else 'Customer'}: {s[0]}" for s in subsequent_msgs]) if subsequent_msgs else "None"

        prompt = f"""
        Role: Senior Auditor for "{group_name}" Delivery Service.
        Task: Evaluate if a pending message needs a NEW alert, can be APPENDED to an existing alert, or is already RESOLVED.

        [Target Message]: "{target_msg}"
        
        [Active Alerts in this Group]:
        {active_context}

        [Subsequent Messages (After Target)]:
        {subsequent_context}

        Rules:
        1. RESOLVE: If subsequent messages (especially from Staff) show the issue is handled.
        2. APPEND: If the target message is about the same topic/order as an active alert.
        3. NEW_ALERT: If it's a new issue that hasn't been alerted yet.

        Output ONLY JSON:
        {{
            "action": "NEW_ALERT" | "APPEND" | "RESOLVE",
            "target_alert_id": alert_msg_id (if APPEND, else null),
            "summary": "Short Burmese summary (3-5 words) if NEW_ALERT"
        }}
        """

        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=20.0
        )
        
        res_data = json.loads(response.choices[0].message.content.strip())
        return res_data.get("action", "NEW_ALERT"), res_data
    except Exception as e:
        log.error(f"❌ Auditor AI Error: {e}")
        return "NEW_ALERT", {"summary": "အကြောင်းပြန်ရန် ကျန်ရှိနေပါသည် (AI Error)"}

def get_routing_data(chat_id, topic_id):
    """ routing_table မှ target chat နှင့် topic ကို ရှာပေးသည် """
    res = db_manager.get_routing_entry(chat_id, topic_id)
    if res:
        return res[0], res[1]
    return MANAGER_ID, 0

def send_new_alert(chat_id, topic_id, msg_id, text, shop_name, summary):
    """ Alert အသစ်ထုတ်ပြန်ခြင်း """
    try:
        target_chat_id, target_topic_id = get_routing_data(chat_id, topic_id)
        
        link_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{link_chat_id}/{msg_id}"
        if topic_id > 0: msg_link += f"?thread={topic_id}"
        
        alert_text = (
            f"🚨 <b>SLA Alert (15 Mins)</b>\n"
            f"🏪 <b>Shop:</b> {shop_name}\n"
            f"📝 <b>Issue:</b> {summary}\n\n"
            f"💬 <b>Message:</b>\n<code>{text}</code>"
        )
        
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("🔗 View", url=msg_link))
        
        sent_msg = bot.send_message(target_chat_id, alert_text, reply_markup=markup, parse_mode="HTML", message_thread_id=target_topic_id if target_topic_id > 0 else None)
        
        # DB Update
        db_manager.update_message_status(msg_id, chat_id, 'ALERTED')
        db_manager.save_alert_id([msg_id], chat_id, sent_msg.message_id, target_chat_id)
        log.info(f"📢 New Alert Sent for {shop_name}: {summary}")
        
    except Exception as e:
        log.error(f"❌ Failed to send new alert: {e}")

def check_escalations():
    """ ၃၀ မိနစ်ကျော်နေသော Alert များကို Manager ဆီ ပို့ခြင်း """
    try:
        conn = db_manager.get_connection()
        threshold = int(time.time()) - 1800
        urgent_msgs = conn.execute(
            "SELECT msg_id, chat_id, topic_id, text FROM message_logs WHERE status='ALERTED' AND timestamp < ?",
            (threshold,)
        ).fetchall()
        conn.close()
        
        for m_id, c_id, t_id, txt in urgent_msgs:
            _, _, shop_name = db_manager.get_topic_context(c_id, t_id)
            
            link_chat_id = str(c_id).replace("-100", "")
            msg_link = f"https://t.me/c/{link_chat_id}/{m_id}"
            
            escalation_text = (
                f"🔥 <b>URGENT ESCALATION (30+ Mins)</b>\n"
                f"🏪 <b>Shop:</b> {shop_name}\n"
                f"💬 <b>Message:</b>\n<code>{txt}</code>"
            )
            
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton("🔗 View", url=msg_link))
            
            bot.send_message(MANAGER_ID, escalation_text, reply_markup=markup, parse_mode="HTML")
            
            # Status ကို ESCALATED သို့ ပြောင်းမည်
            db_manager.update_message_status(m_id, c_id, 'ESCALATED')
            log.warning(f"🔥 Escalated to Manager: {shop_name}")
            
    except Exception as e:
        log.error(f"❌ Escalation Check Error: {e}")

def cleanup_resolved_tickets():
    """ Resolved ဖြစ်သွားသော Ticket များ၏ Alert Message ကို ဖျက်ပြီး Record Group သို့ ပို့ခြင်း """
    try:
        conn = db_manager.get_connection()
        resolved_alerts = conn.execute(
            """SELECT a.alert_msg_id, a.alert_chat_id, m.chat_id, m.msg_id, m.resolved_by, m.text
               FROM alert_tracking a
               JOIN message_logs m ON a.original_msg_id = m.msg_id AND a.chat_id = m.chat_id
               WHERE m.status = 'RESOLVED'"""
        ).fetchall()
        conn.close()

        for a_id, a_chat, c_id, m_id, staff, txt in resolved_alerts:
            # ၁။ Alert Message ကို ဖျက်မည်
            try:
                bot.delete_message(a_chat, a_id)
            except Exception:
                pass

            # ၂။ Record Group သို့ Log ပို့မည်
            _, _, shop_name = db_manager.get_topic_context(c_id, 0)
            record_text = f"✅ <b>{shop_name}</b> - ဖြေရှင်းပြီးပါပြီ\n\n👨‍💻 <b>Staff:</b> {staff}\n💬 <b>Message:</b> {txt}"
            
            try:
                if RECORD_GROUP_ID != 0:
                    bot.send_message(RECORD_GROUP_ID, record_text, parse_mode="HTML")
            except Exception as e:
                log.error(f"Record Log Error: {e}")

            # ၃။ Tracking ထဲမှ ဖယ်ရှားမည်
            conn = db_manager.get_connection()
            conn.execute("DELETE FROM alert_tracking WHERE alert_msg_id = ?", (a_id,))
            conn.commit()
            conn.close()
            log.info(f"🧹 Cleaned up resolved alert for {shop_name}")

    except Exception as e:
        log.error(f"❌ Cleanup Error: {e}")

def process_audits():
    """ Main Auditor Loop """
    log.info("🧠 Auditor (Worker 2: AI Brain) is running...")
    
    while True:
        try:
            if not is_office_hours():
                time.sleep(60)
                continue

            # ၁။ ၁၅ မိနစ်ကျော်နေသော Pending စာများကို ရှာမည်
            pending_msgs = db_manager.get_pending_messages(minutes=15)
            
            for msg_id, chat_id, topic_id, text, ts in pending_msgs:
                active_alerts = db_manager.get_active_alerts_for_group(chat_id, topic_id)
                subsequent_msgs = db_manager.get_messages_after(chat_id, topic_id, msg_id)
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                action, ai_res = evaluate_with_ai(shop_name, text, active_alerts, subsequent_msgs)
                
                if action == "RESOLVE":
                    db_manager.update_message_status(msg_id, chat_id, 'RESOLVED')
                    log.info(f"✨ AI Auto-Resolved: {msg_id} in {shop_name}")
                elif action == "APPEND" and ai_res.get("target_alert_id"):
                    db_manager.update_message_status(msg_id, chat_id, 'ALERTED')
                    alert_msg_id = ai_res["target_alert_id"]
                    db_manager.save_alert_id([msg_id], chat_id, alert_msg_id, 0) # target_chat_id will be handled by tracking
                    log.info(f"📎 AI Appended: {msg_id} to Alert {alert_msg_id}")
                else:
                    summary = ai_res.get("summary", "အကြောင်းပြန်ရန် ကျန်ရှိနေပါသည်")
                    send_new_alert(chat_id, topic_id, msg_id, text, shop_name, summary)

            check_escalations()
            cleanup_resolved_tickets()
            time.sleep(30)

        except Exception as e:
            log.error(f"⚠️ Auditor Loop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    process_audits()
