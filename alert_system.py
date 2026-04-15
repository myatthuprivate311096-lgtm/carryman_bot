# Version: 4.0 (Anti-Loop Master & Smart Receipt System)
from logger import log
import time
import threading
import json
import os
import telebot
import re
import requests
from datetime import datetime, timedelta
from telebot import types
import db_manager

try:
    import ai_engine
except ImportError:
    ai_engine = None

# Environment Variables
MANAGER_ID = int(os.getenv('MANAGER_ID', 0))
ALERT_CHAT_ID = int(os.getenv('ALERT_CHAT_ID', MANAGER_ID))
ALERT_TOPIC_ID = int(os.getenv('ALERT_TOPIC_ID', 0))
ARCHIVE_CHAT_ID = int(os.getenv('ARCHIVE_CHAT_ID', 0)) 
ARCHIVE_TOPIC_ID = int(os.getenv('ARCHIVE_TOPIC_ID', 0)) 
HEALTHCHECK_URL = os.getenv('HEALTHCHECK_URL')

def clean_text(text):
    return re.sub(r'[_*[\]()~`>#+\-=|{}.!]', ' ', str(text))

def get_safe_thread_id(topic_id):
    """ Topic ID က 0 သို့မဟုတ် 1 ဖြစ်နေရင် None (General) အဖြစ် ပြောင်းပေးမည် """
    try:
        t_id = int(topic_id)
        return t_id if t_id > 1 else None
    except:
        return None

# ==========================================
# 🚨 ၁။ Instant Alert (Manual /alert)
# ==========================================
def handle_instant_alert(bot, chat_id, topic_id, staff_name, issue_text, original_text="", msg_link=""):
    TARGET_CHAT_ID = int(os.getenv('ALERT_CHAT_ID', MANAGER_ID))
    RAW_TOPIC_ID = int(os.getenv('ALERT_TOPIC_ID', 0))

    try:
        shop_name = db_manager.get_topic_context(chat_id, topic_id)[2]
    except:
        shop_name = "Unknown Shop"

    alert_text = f"🚨 **INSTANT ALERT (Manual)**\n🏪 **Shop:** {shop_name}\n👨‍💻 **Called By:** {staff_name}\n📝 **Note:** {clean_text(issue_text)}"
    
    if original_text:
        alert_text += f"\n\n💬 **Customer Message:**\n`{clean_text(original_text)}`"

    markup = types.InlineKeyboardMarkup()
    if msg_link:
        markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))
    markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"done_{chat_id}"))

    try:
        thread_id = get_safe_thread_id(RAW_TOPIC_ID)
        sent_msg = bot.send_message(TARGET_CHAT_ID, alert_text, reply_markup=markup, parse_mode="Markdown", message_thread_id=thread_id)
        
        # 💡 Manual Alert ကို Manager ဆီ Escalation တက်စေရန် (၁၅ မိနစ် အတုပြုလုပ်၍ မှတ်ခြင်း)
        pseudo_msg_id = -int(time.time() * 1000)
        escalation_timer = int(time.time()) - 900 
        
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO message_logs (msg_id, chat_id, topic_id, user_id, text, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, 'alerted')",
                     (pseudo_msg_id, chat_id, topic_id, 0, f"[Manual] {issue_text}", escalation_timer))
        conn.commit()
        conn.close()

        # Database တွင် Alert Tracking ချိတ်ဆက်ခြင်း
        db_manager.save_alert_id([pseudo_msg_id], chat_id, sent_msg.message_id, TARGET_CHAT_ID)
        log.info(f"✅ Instant Alert sent successfully.")
    except Exception as e:
        log.error(f"❌ Instant Alert Send Error: {e}")

# ==========================================
# ⏳ ၂။ SLA Watchdog (Anti-Loop Master)
# ==========================================
def check_and_alert(bot):
    conn = db_manager.get_connection()
    c = conn.cursor()
    now = int(time.time())

    limit_15 = now - 900
    try:
        c.execute("SELECT DISTINCT chat_id, topic_id FROM message_logs WHERE status='pending' AND timestamp < ?", (limit_15,))
        baskets_15 = c.fetchall()
    except Exception as e:
        log.error(f"Watchdog DB Fetch Error: {e}")
        conn.close()
        return

    for chat_id, topic_id in baskets_15:
        c.execute("SELECT msg_id, text, timestamp FROM message_logs WHERE chat_id=? AND topic_id=? AND status='pending'", (chat_id, topic_id))
        pending_msgs = c.fetchall()
        if not pending_msgs: continue

        c.execute("SELECT text FROM message_logs WHERE chat_id=? AND topic_id=? AND status='resolved' ORDER BY timestamp DESC LIMIT 5", (chat_id, topic_id))
        resolved_msgs = [r[0] for r in c.fetchall()]

        try:
            c.execute("SELECT shop_name FROM os_groups WHERE chat_id=?", (chat_id,))
        except:
            c.execute("SELECT group_name FROM os_groups WHERE group_id=?", (chat_id,))
        g_res = c.fetchone()
        group_name = g_res[0] if g_res else "Unknown Shop"

        try:
            if ai_engine:
                ai_res_raw = ai_engine.analyze_context(group_name, pending_msgs, resolved_msgs)
            else:
                ai_res_raw = json.dumps({"issues": [{"issue": "အရေးကြီးကိစ္စ (SLA Overdue)", "msg_ids": [m[0] for m in pending_msgs]}]})
        except Exception:
            ai_res_raw = json.dumps({"issues": [{"issue": "အရေးကြီးကိစ္စ (AI Error)", "msg_ids": [m[0] for m in pending_msgs]}]})

        if not ai_res_raw:
            c.execute("UPDATE message_logs SET status='ignored' WHERE chat_id=? AND status='pending'", (chat_id,))
            continue

        try:
            ai_data = json.loads(ai_res_raw)
            issues = ai_data.get("issues", [])
        except:
            issues = []

        if issues:
            for item in issues:
                issue_text = item.get("issue", "အရေးကြီးကိစ္စ")
                alert_ids = item.get("msg_ids", [])
                ticket_msgs = [m for m in pending_msgs if m[0] in alert_ids]
                if not ticket_msgs: continue

                link_chat_id = str(chat_id).replace("-100", "")
                msg_link = f"https://t.me/c/{link_chat_id}/{ticket_msgs[0][0]}"
                if topic_id > 1: msg_link += f"?thread={topic_id}"

                alert_text = f"🚨 **SLA Alert (15 Mins)**\n🏪 **Shop:** {group_name}\n📝 **Issue:** {clean_text(issue_text)}\n\n📥 **Messages:**\n"
                for m in ticket_msgs:
                    mm_time = datetime.fromtimestamp(m[2]) + timedelta(hours=6, minutes=30)
                    alert_text += f"• `[{mm_time.strftime('%I:%M %p')}]` {clean_text(m[1])}\n"

                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔗 View", url=msg_link),
                           types.InlineKeyboardButton("✅ Done", callback_data=f"done_{chat_id}"))

                try:
                    thread_id = get_safe_thread_id(ALERT_TOPIC_ID)
                    sent_msg = bot.send_message(ALERT_CHAT_ID, alert_text, reply_markup=markup, parse_mode="Markdown", message_thread_id=thread_id)
                    
                    # 💡 FIX: DB Lock မဖြစ်စေရန် Direct Execute သုံးမည်
                    safe_alert_ids = [m[0] for m in ticket_msgs]
                    for m_id in safe_alert_ids:
                        c.execute("INSERT INTO alert_tracking VALUES (?, ?, ?, ?)", (m_id, chat_id, sent_msg.message_id, ALERT_CHAT_ID))
                        c.execute("UPDATE message_logs SET status='alerted' WHERE msg_id=? AND chat_id=?", (m_id, chat_id))
                except Exception as e: log.error(f"Alert Send Error: {e}")

            all_alerted = [m_id for item in issues for m_id in item.get("msg_ids", [])]
            for m in pending_msgs:
                if m[0] not in all_alerted:
                    c.execute("UPDATE message_logs SET status='ignored' WHERE msg_id=? AND chat_id=?", (m[0], chat_id))
        else:
            c.execute("UPDATE message_logs SET status='ignored' WHERE chat_id=? AND status='pending'", (chat_id,))
        conn.commit()

    # --- (ခ) ၃၀ မိနစ်ပြည့်သော Alert များကို Manager ဆီ Escalation တက်ခြင်း ---
    limit_30 = now - 1800
    c.execute("SELECT msg_id, chat_id, topic_id, text, timestamp FROM message_logs WHERE status='alerted' AND timestamp < ?", (limit_30,))
    urgent_msgs = c.fetchall()

    if urgent_msgs:
        urgent_groups = {}
        for m_id, c_id, t_id, txt, ts in urgent_msgs:
            if c_id not in urgent_groups:
                try: c.execute("SELECT shop_name FROM os_groups WHERE chat_id=?", (c_id,))
                except: c.execute("SELECT group_name FROM os_groups WHERE group_id=?", (c_id,))
                g_res = c.fetchone()
                urgent_groups[c_id] = {'topic_id': t_id, 'shop': g_res[0] if g_res else "Unknown", 'msgs': [], 'ids': [], 'first_id': m_id}

            mm_time = datetime.fromtimestamp(ts) + timedelta(hours=6, minutes=30)
            urgent_groups[c_id]['msgs'].append(f"• `[{mm_time.strftime('%I:%M %p')}]` {clean_text(txt)}")
            urgent_groups[c_id]['ids'].append(m_id)

        for c_id, data in urgent_groups.items():
            link_chat_id = str(c_id).replace("-100", "")
            urgent_link = f"https://t.me/c/{link_chat_id}/{data['first_id']}"
            if data['topic_id'] > 1: urgent_link += f"?thread={data['topic_id']}"

            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔗 View", url=urgent_link),
                       types.InlineKeyboardButton("✅ Done", callback_data=f"done_{c_id}"))

            try:
                sent_msg = bot.send_message(MANAGER_ID, f"🔥 **URGENT (30+ Mins)**\n🏪 **Shop:** {data['shop']}\n\n" + "\n".join(data['msgs']), reply_markup=markup, parse_mode="Markdown")
                # 💡 FIX: DB Lock မဖြစ်စေရန် Direct Update သုံးမည်
                for m_id in data['ids']:
                    c.execute("INSERT INTO alert_tracking VALUES (?, ?, ?, ?)", (m_id, c_id, sent_msg.message_id, MANAGER_ID))
                    c.execute("UPDATE message_logs SET status='escalated' WHERE msg_id=? AND chat_id=?", (m_id, c_id))
            except Exception as e: log.error(f"Escalation Error: {e}")
        conn.commit()

    conn.close()

# ==========================================
# ✅ ၃။ Resolution (Ticket ပိတ်သိမ်းခြင်း & Smart Receipt)
# ==========================================
def update_resolved_alerts(bot, msg_id, chat_id, staff_name):
    conn = db_manager.get_connection()
    c = conn.cursor()

    # 💡 Customer ၏ မူရင်းစာနှင့် ကြာချိန်ကို တွက်ချက်မည်
    c.execute("SELECT text, timestamp FROM message_logs WHERE chat_id=? AND status IN ('pending', 'alerted', 'escalated') ORDER BY timestamp ASC LIMIT 1", (chat_id,))
    first_msg = c.fetchone()
    
    issue_text = "N/A"
    time_taken_str = "0 Mins"
    
    if first_msg:
        raw_text = first_msg[0]
        issue_text = (raw_text[:60] + '...') if len(raw_text) > 60 else raw_text
        start_time = first_msg[1]
        end_time = int(time.time())
        duration_mins = max(1, int((end_time - start_time) / 60))
        
        if duration_mins >= 60:
            hours = duration_mins // 60
            mins = duration_mins % 60
            time_taken_str = f"{hours} Hrs {mins} Mins"
        else:
            time_taken_str = f"{duration_mins} Mins"

    # Database တွင် အလုံးစုံ ရှင်းလင်းမည် (pending, alerted, escalated အားလုံး)
    c.execute("UPDATE message_logs SET status='resolved', resolved_by=? WHERE chat_id=? AND status IN ('pending', 'alerted', 'escalated')", (staff_name, chat_id))
    conn.commit()

    if msg_id == 0:
        c.execute("SELECT alert_msg_id, alert_chat_id FROM alert_tracking WHERE chat_id=?", (chat_id,))
    else:
        c.execute("SELECT alert_msg_id, alert_chat_id FROM alert_tracking WHERE (original_msg_id=? OR original_msg_id=0) AND chat_id=?", (msg_id, chat_id))
    alerts = c.fetchall()

    for a_id, a_chat in alerts:
        try: bot.delete_message(a_chat, a_id)
        except: pass

    if msg_id == 0:
        c.execute("DELETE FROM alert_tracking WHERE chat_id=?", (chat_id,))
    else:
        c.execute("DELETE FROM alert_tracking WHERE (original_msg_id=? OR original_msg_id=0) AND chat_id=?", (msg_id, chat_id))
    conn.commit()

    if ARCHIVE_CHAT_ID != 0:
        try: c.execute("SELECT shop_name FROM os_groups WHERE chat_id=?", (chat_id,))
        except: c.execute("SELECT group_name FROM os_groups WHERE group_id=?", (chat_id,))
        g_res = c.fetchone()
        group_name = g_res[0] if g_res else "Unknown Shop"
        
        # 💡 [NEW] Archive ပြေစာ ပုံစံအသစ် (Issue အစား Customer ဖြင့် ပြမည်)
        resolved_text = (
            "✅ **[TICKET RESOLVED]**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🏪 **Shop:** {group_name}\n"
            f"💬 **Customer:** {clean_text(issue_text)}\n"
            f"⏱️ **Time Taken:** {time_taken_str}\n"
            f"👨‍💻 **Resolved By:** {staff_name}\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        try: 
            thread_id = get_safe_thread_id(ARCHIVE_TOPIC_ID)
            bot.send_message(ARCHIVE_CHAT_ID, resolved_text, parse_mode="Markdown", message_thread_id=thread_id)
        except: pass

    conn.close()

# ==========================================
# ⚙️ ၄။ Handlers & Timers
# ==========================================
def register_handlers(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith("done_"))
    def handle_done(call):
        try:
            # ၁။ Telegram Server ကို ချက်ချင်း အကြောင်းပြန်မည်
            bot.answer_callback_query(call.id, "✅ ရှင်းလင်းပြီးပါပြီ!")
            
            chat_id = int(call.data.split("_")[-1])
            user_id = call.from_user.id
            
            # ၂။ Database မှ ဝန်ထမ်းအချက်အလက် အပြည့်အစုံကို ဆွဲယူခြင်း
            staff_data = db_manager.get_staff_info(user_id)
            if staff_data:
                staff_name = f"{staff_data[1]} ({staff_data[2]} - {staff_data[3]})"
            else:
                staff_name = f"{call.from_user.first_name} (Manager/Unknown)"
            
            # ၃။ Loading လည်နေသော Alert စာကိုယ်တိုင်အား တိုက်ရိုက် ဖျက်ပစ်မည်
            try: bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass
            
            # ၄။ Database အား Update လုပ်ပြီး Archive သို့ ပို့မည်
            db_manager.resolve_message(0, chat_id, staff_name)
            update_resolved_alerts(bot, 0, chat_id, staff_name)
            
        except Exception as e:
            log.error(f"Callback Done Error: {e}")

def watchdog_timer(bot):
    while True:
        try:
            check_and_alert(bot)
            if HEALTHCHECK_URL: requests.get(HEALTHCHECK_URL, timeout=10)
        except Exception as e: 
            log.error(f"Watchdog Error: {e}")
        time.sleep(60)

def start_watchdog(bot):
    threading.Thread(target=watchdog_timer, args=(bot,), daemon=True).start()