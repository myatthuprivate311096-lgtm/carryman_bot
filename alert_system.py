# Version: 4.0 (Anti-Loop Master & Smart Receipt System)
from logger import log
import time
import threading
import json
import os
import html
import telebot
import re
import requests
from datetime import datetime, timedelta
from telebot import types
import db_manager
from collections import defaultdict

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

def get_routing_data(chat_id, topic_id):
    """
    routing_table ထဲမှာ os chat/topic အတွက် alert route ရှာပေးမည်။
    မတွေ့ပါက .env default ALERT IDs ကို fallback သုံးမည်။
    """
    default_chat_id = int(os.getenv('ALERT_CHAT_ID', MANAGER_ID))
    default_topic_id = int(os.getenv('ALERT_TOPIC_ID', 0))
    try:
        route = db_manager.get_routing_entry(chat_id, topic_id)
        if route and route[0]:
            return int(route[0]), int(route[1] or 0)
    except Exception as e:
        log.warning(f"Routing lookup failed for ({chat_id}, {topic_id}): {e}")
    return default_chat_id, default_topic_id

# ==========================================
# 🚨 ၁။ Instant Alert (Manual /alert)
# ==========================================
def handle_instant_alert(bot, chat_id, topic_id, staff_name, issue_text, original_text="", msg_link="", original_msg_id=0):
    TARGET_CHAT_ID, RAW_TOPIC_ID = get_routing_data(chat_id, topic_id)

    try:
        shop_name = db_manager.get_topic_context(chat_id, topic_id)[2]
    except:
        shop_name = "Unknown Shop"

    safe_shop = html.escape(str(shop_name))
    safe_staff = html.escape(str(staff_name))
    safe_issue = html.escape(str(issue_text))
    
    alert_text = f"🚨 <b>INSTANT ALERT (Manual)</b>\n🏪 <b>Shop:</b> {safe_shop}\n👨‍💻 <b>Called By:</b> {safe_staff}\n📝 <b>Note:</b> {safe_issue}"
    
    if original_text:
        safe_original = html.escape(str(original_text))
        alert_text += f"\n\n💬 <b>Customer Message:</b>\n<code>{safe_original}</code>"

    markup = types.InlineKeyboardMarkup()
    if msg_link:
        markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))
    markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"done_{chat_id}"))

    try:
        thread_id = get_safe_thread_id(RAW_TOPIC_ID)
        sent_msg = bot.send_message(TARGET_CHAT_ID, alert_text, reply_markup=markup, parse_mode="HTML", message_thread_id=thread_id)
        
        # 💡 Manual Alert ကို Manager ဆီ Escalation တက်စေရန် (၁၅ မိနစ် အတုပြုလုပ်၍ မှတ်ခြင်း)
        # 💡 original_msg_id မပါလာပါက pseudo ID သုံးမည်
        final_msg_id = original_msg_id if original_msg_id != 0 else -int(time.time() * 1000)
        escalation_timer = int(time.time()) - 900
        
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO message_logs (msg_id, chat_id, topic_id, user_id, text, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, 'alerted')",
                     (final_msg_id, chat_id, topic_id, 0, issue_text, escalation_timer))
        conn.commit()
        conn.close()

        # Database တွင် Alert Tracking ချိတ်ဆက်ခြင်း
        db_manager.save_alert_id([final_msg_id], chat_id, sent_msg.message_id, TARGET_CHAT_ID)
        log.info(f"✅ Instant Alert sent successfully.")
    except Exception as e:
        log.error(f"❌ Instant Alert Send Error: {e}")

# ==========================================
# ⏳ ၂။ SLA Watchdog (Anti-Loop Master)
# ==========================================
def check_and_alert(bot):
    conn = db_manager.get_connection()
    c = conn.cursor()
    # DB timestamp များနှင့်တူညီစေရန် SQLite UTC epoch ကို source of truth အဖြစ်သုံးသည်
    c.execute("SELECT CAST(strftime('%s','now') AS INTEGER)")
    now = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM message_logs WHERE status='pending'")
    total_pending = c.fetchone()[0]
    log.info(f"Total pending messages: {total_pending}")

    limit_15 = now - 900
    try:
        c.execute("SELECT DISTINCT chat_id, topic_id FROM message_logs WHERE status='pending' AND timestamp < ?", (limit_15,))
        baskets_15 = c.fetchall()
        log.info(f"Watchdog eligible baskets (>15m): {len(baskets_15)}")
    except Exception as e:
        log.error(f"Watchdog DB Fetch Error: {e}")
        conn.close()
        return

    basket_keys = [(int(chat_id), int(topic_id)) for chat_id, topic_id in baskets_15]

    # N+1 query လျှော့ချရန် basket တွေအတွက် pending/resolved/shop data ကို တစ်ခါတည်း bulk fetch
    pending_map = defaultdict(list)
    resolved_map = defaultdict(list)
    shop_map = {}

    if basket_keys:
        where_clause = " OR ".join(["(chat_id=? AND topic_id=?)"] * len(basket_keys))
        flat_params = [v for pair in basket_keys for v in pair]

        c.execute(
            f"SELECT chat_id, topic_id, msg_id, text, timestamp FROM message_logs WHERE status='pending' AND ({where_clause})",
            flat_params
        )
        for c_id, t_id, m_id, txt, ts in c.fetchall():
            pending_map[(c_id, t_id)].append((m_id, txt, ts))

        c.execute(
            f"SELECT chat_id, topic_id, text FROM message_logs WHERE status='resolved' AND ({where_clause}) ORDER BY timestamp DESC",
            flat_params
        )
        for c_id, t_id, txt in c.fetchall():
            key = (c_id, t_id)
            if len(resolved_map[key]) < 5:
                resolved_map[key].append(txt)

        unique_chat_ids = sorted({chat_id for chat_id, _ in basket_keys})
        in_clause = ",".join(["?"] * len(unique_chat_ids))
        c.execute(
            f"SELECT chat_id, shop_name FROM os_groups WHERE chat_id IN ({in_clause}) ORDER BY rowid DESC",
            unique_chat_ids
        )
        for c_id, s_name in c.fetchall():
            if c_id not in shop_map and s_name:
                shop_map[c_id] = db_manager.clean_shop_name(s_name)

    for chat_id, topic_id in basket_keys:
        pending_msgs = pending_map.get((chat_id, topic_id), [])
        if not pending_msgs:
            continue

        resolved_msgs = resolved_map.get((chat_id, topic_id), [])
        group_name = shop_map.get(chat_id, "Unknown Shop")
        if group_name == "Unknown Shop":
            log.warning(f"Shop map not found/invalid for chat_id={chat_id}; continuing alert with fallback shop name.")

        oldest_ts = min(m[2] for m in pending_msgs)
        age_mins = round((now - int(oldest_ts)) / 60, 2)
        log.info(f"Basket age check chat_id={chat_id}, topic_id={topic_id}, oldest_age_mins={age_mins}")

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

                safe_group = html.escape(str(group_name))
                safe_issue = html.escape(str(issue_text))
                alert_text = f"🚨 <b>SLA Alert (15 Mins)</b>\n🏪 <b>Shop:</b> {safe_group}\n📝 <b>Issue:</b> {safe_issue}\n\n📥 <b>Messages:</b>\n"
                for m in ticket_msgs:
                    mm_time = datetime.fromtimestamp(m[2]) + timedelta(hours=6, minutes=30)
                    safe_m_text = html.escape(str(m[1]))
                    alert_text += f"• <code>[{mm_time.strftime('%I:%M %p')}]</code> {safe_m_text}\n"

                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔗 View", url=msg_link),
                           types.InlineKeyboardButton("✅ Done", callback_data=f"done_{chat_id}"))

                try:
                    route_data = db_manager.get_routing_entry(chat_id, topic_id)
                    if not route_data:
                        log.warning(f"Routing map not found for chat_id={chat_id}, topic_id={topic_id}; fallback route will be used.")
                    target_chat_id, target_topic_id = get_routing_data(chat_id, topic_id)
                    thread_id = get_safe_thread_id(target_topic_id)
                    sent_msg = bot.send_message(target_chat_id, alert_text, reply_markup=markup, parse_mode="HTML", message_thread_id=thread_id)
                    
                    # 💡 FIX: DB Lock မဖြစ်စေရန် Direct Execute သုံးမည်
                    safe_alert_ids = [m[0] for m in ticket_msgs]
                    for m_id in safe_alert_ids:
                        c.execute(
                            "INSERT INTO alert_tracking (original_msg_id, chat_id, alert_msg_id, alert_chat_id, created_at) VALUES (?, ?, ?, ?, ?)",
                            (m_id, chat_id, sent_msg.message_id, target_chat_id, int(time.time()))
                        )
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
            safe_txt = html.escape(str(txt))
            urgent_groups[c_id]['msgs'].append(f"• <code>[{mm_time.strftime('%I:%M %p')}]</code> {safe_txt}")
            urgent_groups[c_id]['ids'].append(m_id)

        for c_id, data in urgent_groups.items():
            link_chat_id = str(c_id).replace("-100", "")
            urgent_link = f"https://t.me/c/{link_chat_id}/{data['first_id']}"
            if data['topic_id'] > 1: urgent_link += f"?thread={data['topic_id']}"

            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔗 View", url=urgent_link),
                       types.InlineKeyboardButton("✅ Done", callback_data=f"done_{c_id}"))

            try:
                safe_shop = html.escape(str(data['shop']))
                sent_msg = bot.send_message(MANAGER_ID, f"🔥 <b>URGENT (30+ Mins)</b>\n🏪 <b>Shop:</b> {safe_shop}\n\n" + "\n".join(data['msgs']), reply_markup=markup, parse_mode="HTML")
                # 💡 FIX: DB Lock မဖြစ်စေရန် Direct Update သုံးမည်
                for m_id in data['ids']:
                    c.execute(
                        "INSERT INTO alert_tracking (original_msg_id, chat_id, alert_msg_id, alert_chat_id, created_at) VALUES (?, ?, ?, ?, ?)",
                        (m_id, c_id, sent_msg.message_id, MANAGER_ID, int(time.time()))
                    )
                    c.execute("UPDATE message_logs SET status='escalated' WHERE msg_id=? AND chat_id=?", (m_id, c_id))
            except Exception as e: log.error(f"Escalation Error: {e}")
        conn.commit()

    conn.close()

# ==========================================
# ✅ ၃။ Resolution (Ticket ပိတ်သိမ်းခြင်း & Smart Receipt)
# ==========================================
def update_resolved_alerts(bot, msg_id, chat_id, staff_name, alert_msg_id=None):
    conn = db_manager.get_connection()
    c = conn.cursor()
    resolved_scope = "chat" if msg_id == 0 and not alert_msg_id else "message"
    log.info(f"[resolve] start scope={resolved_scope} chat_id={chat_id} msg_id={msg_id} alert_msg_id={alert_msg_id} staff={staff_name}")

    # Resolution report အတွက် context များ စုစည်းခြင်း
    resolved_at = int(time.time())
    issue_text = "N/A"
    topic_name = "N/A"
    group_name = "Unknown Shop"
    time_taken_str = "Under 1 Min"
    msg_link = ""

    try:
        first_topic_id = None
        actual_msg_id = msg_id
        actual_chat_id = chat_id

        # ၁။ Alert Message ID ဖြင့် တိကျသော Data ကို အရင်ရှာမည်
        if alert_msg_id:
            c.execute(
                """SELECT m.text, m.topic_id, m.msg_id, m.chat_id
                   FROM message_logs m
                   JOIN alert_tracking a ON m.msg_id = a.original_msg_id AND m.chat_id = a.chat_id
                   WHERE a.alert_msg_id = ? LIMIT 1""",
                (alert_msg_id,)
            )
            res = c.fetchone()
            if res:
                issue_text = res[0]
                first_topic_id = res[1]
                actual_msg_id = res[2]
                actual_chat_id = res[3]
        
        # ၂။ ရှာမတွေ့ပါက သို့မဟုတ် alert_msg_id မပါပါက Fallback အနေဖြင့် ရှာမည်
        if issue_text == "N/A":
            target_params = (chat_id,) if msg_id == 0 else (chat_id, msg_id)
            target_filter_sql = "chat_id=? AND status IN ('pending', 'alerted', 'escalated')" if msg_id == 0 else "chat_id=? AND msg_id=? AND status IN ('pending', 'alerted', 'escalated')"
            c.execute(
                f"SELECT text, topic_id, msg_id FROM message_logs WHERE {target_filter_sql} ORDER BY timestamp ASC LIMIT 1",
                target_params
            )
            first_msg = c.fetchone()
            if first_msg:
                issue_text = first_msg[0]
                first_topic_id = first_msg[1]
                actual_msg_id = first_msg[2]

        # Link တည်ဆောက်ခြင်း
        clean_cid = str(actual_chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_cid}/{actual_msg_id}"
        if first_topic_id and first_topic_id > 1:
            msg_link += f"?thread={first_topic_id}"

        # Time Taken တွက်ချက်ခြင်း
        if alert_msg_id:
            c.execute("SELECT created_at FROM alert_tracking WHERE alert_msg_id=? LIMIT 1", (alert_msg_id,))
        elif msg_id == 0:
            c.execute("SELECT MIN(created_at) FROM alert_tracking WHERE chat_id=?", (chat_id,))
        else:
            c.execute("SELECT MIN(created_at) FROM alert_tracking WHERE chat_id=? AND original_msg_id=?", (chat_id, msg_id))
        
        alert_created_row = c.fetchone()
        alert_created_at = alert_created_row[0] if alert_created_row and alert_created_row[0] else None
        if alert_created_at:
            elapsed_seconds = max(0, resolved_at - int(alert_created_at))
            if elapsed_seconds < 60:
                time_taken_str = f"{elapsed_seconds}s"
            elif elapsed_seconds < 3600:
                time_taken_str = f"{int(elapsed_seconds / 60)}m"
            else:
                hours = int(elapsed_seconds / 3600)
                mins = int((elapsed_seconds % 3600) / 60)
                time_taken_str = f"{hours}h {mins}m"

        if first_topic_id is not None:
            # ၁။ Routing Table မှ Department Name ကို အရင်ရှာမည် (တိကျသော Mapping အတွက်)
            c.execute(
                "SELECT department_name FROM routing_table WHERE os_chat_id=? AND os_topic_id=? LIMIT 1",
                (chat_id, first_topic_id)
            )
            route_row = c.fetchone()
            if route_row and route_row[0]:
                topic_name = route_row[0]
            else:
                # ၂။ Routing Table တွင် မရှိပါက os_groups မှ ရှာမည်
                c.execute(
                    "SELECT topic_name FROM os_groups WHERE chat_id=? AND topic_id=? ORDER BY rowid DESC LIMIT 1",
                    (chat_id, first_topic_id)
                )
                topic_row = c.fetchone()
                if topic_row and topic_row[0]:
                    topic_name = topic_row[0]

        if topic_name == "N/A":
            c.execute("SELECT topic_name FROM os_groups WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (chat_id,))
            fallback_topic = c.fetchone()
            if fallback_topic and fallback_topic[0]:
                topic_name = fallback_topic[0]

        c.execute("SELECT shop_name FROM os_groups WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (chat_id,))
        shop_row = c.fetchone()
        if shop_row and shop_row[0]:
            group_name = shop_row[0]
    except Exception as e:
        log.error(f"Resolved context fetch error: {e}")

    # Database တွင် အလုံးစုံ ရှင်းလင်းမည် (pending, alerted, escalated အားလုံး)
    # resolve write ကို ဒီ function တစ်နေရာတည်းမှာပဲ centralize လုပ်ထားသည်
    if msg_id == 0:
        c.execute(
            "UPDATE message_logs SET status='resolved', resolved_by=?, resolve_time=? WHERE chat_id=? AND status IN ('pending', 'alerted', 'escalated')",
            (staff_name, resolved_at, chat_id)
        )
    else:
        c.execute(
            "UPDATE message_logs SET status='resolved', resolved_by=?, resolve_time=? WHERE chat_id=? AND msg_id=? AND status IN ('pending', 'alerted', 'escalated')",
            (staff_name, resolved_at, chat_id, msg_id)
        )
    log.info(f"[resolve] updated_rows={c.rowcount} scope={resolved_scope} chat_id={chat_id} msg_id={msg_id}")
    conn.commit()

    if msg_id == 0:
        c.execute("SELECT alert_msg_id, alert_chat_id FROM alert_tracking WHERE chat_id=?", (chat_id,))
    else:
        c.execute("SELECT alert_msg_id, alert_chat_id FROM alert_tracking WHERE (original_msg_id=? OR original_msg_id=0) AND chat_id=?", (msg_id, chat_id))
    alerts = c.fetchall()

    log.info(f"[resolve] linked_alerts={len(alerts)} scope={resolved_scope} chat_id={chat_id} msg_id={msg_id}")

    for a_id, a_chat in alerts:
        try:
            bot.delete_message(a_chat, a_id)
        except Exception as e:
            log.error(f"Delete Error in update_resolved_alerts: {e}")

    if msg_id == 0:
        c.execute("DELETE FROM alert_tracking WHERE chat_id=?", (chat_id,))
    else:
        c.execute("DELETE FROM alert_tracking WHERE (original_msg_id=? OR original_msg_id=0) AND chat_id=?", (msg_id, chat_id))
    log.info(f"[resolve] alert_tracking_deleted_rows={c.rowcount} scope={resolved_scope} chat_id={chat_id} msg_id={msg_id}")
    conn.commit()

    if ARCHIVE_CHAT_ID != 0:
        resolved_at_text = (datetime.fromtimestamp(resolved_at) + timedelta(hours=6, minutes=30)).strftime("%d-%b-%Y %I:%M %p")
        
        # HTML tags များဖယ်ရှားပြီး Raw Text သီးသန့်သုံးမည်
        safe_group = str(group_name)
        safe_topic = str(topic_name)
        safe_staff = str(staff_name)
        safe_issue = str(issue_text)

        resolved_text = (
            "✅ <b>TICKET RESOLVED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🏪 Shop: {safe_group}\n"
            f"🏷️ Department: {safe_topic}\n"
            f"💬 Message: {safe_issue}\n"
            f"⏱️ Time Taken: {time_taken_str}\n"
            f"🔗 Link: {msg_link}\n"
            f"👨‍💻 Staff: {safe_staff}\n"
            f"📅 Resolved At: {resolved_at_text}\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        try:
            thread_id = get_safe_thread_id(ARCHIVE_TOPIC_ID)
            bot.send_message(ARCHIVE_CHAT_ID, resolved_text, parse_mode="HTML", message_thread_id=thread_id)
        except Exception as e:
            log.error(f"Archive resolved message send error: {e}")

    log.info(f"[resolve] complete scope={resolved_scope} chat_id={chat_id} msg_id={msg_id}")
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
            alert_msg_id = call.message.message_id
            user_id = call.from_user.id
            
            # ၂။ Database မှ ဝန်ထမ်းအချက်အလက် အပြည့်အစုံကို ဆွဲယူခြင်း
            staff_data = db_manager.get_staff_info(user_id)
            staff_name = f"{staff_data[1]}" if staff_data else f"{call.from_user.first_name}"
            
            try:
                bot.delete_message(call.message.chat.id, alert_msg_id)
            except Exception as e:
                log.error(f"Delete Error in handle_done: {e}")
            
            # ၃။ Alert Tracking မှတစ်ဆင့် မူရင်း Message ID ကို ရှာဖွေခြင်း
            conn = db_manager.get_connection()
            res = conn.execute("SELECT original_msg_id FROM alert_tracking WHERE alert_msg_id=? LIMIT 1", (alert_msg_id,)).fetchone()
            conn.close()
            
            orig_id = res[0] if res else 0
            
            # ၄။ Database အား Update လုပ်ပြီး Archive သို့ ပို့မည်
            db_manager.resolve_message(orig_id, chat_id, staff_name, method='Button')
            update_resolved_alerts(bot, orig_id, chat_id, f"{staff_name} (Button)", alert_msg_id=alert_msg_id)
            
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