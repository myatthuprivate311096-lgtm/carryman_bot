# Version: 4.0 (2-Worker Architecture Ready)
import sqlite3
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from logger import log

# 💡 Absolute Path Fix - Locked to script directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'carryman.db')
ENV_FILE = os.path.join(BASE_DIR, '.env')

# Load environment variables using absolute path
load_dotenv(ENV_FILE)

def clean_shop_name(raw_name):
    """
    Shop name မှ emoji/non-ascii noise များကိုဖြတ်၍ base name ပြန်ပေးမည်။
    🤝 ပါဝင်ပါက ၎င်း၏ ရှေ့က အမည်ကိုသာ ယူမည်။
    """
    if raw_name is None:
        return "Unknown Shop"
    
    # 🤝 ဖြင့် ခွဲထုတ်ခြင်း
    raw_str = str(raw_name)
    if '🤝' in raw_str:
        base = raw_str.split('🤝')[0]
    else:
        base = raw_str
        
    # Emoji နှင့် Non-ASCII များကို ဖယ်ရှားခြင်း
    ascii_only = base.encode('ascii', 'ignore').decode('utf-8')
    
    # ပိုနေသော space များကို ရှင်းလင်းခြင်း
    cleaned = " ".join(ascii_only.split()).strip()
    
    return cleaned or "Unknown Shop"

def get_connection():
    """ Database ချိတ်ဆက်မှုအား Multithreading နှင့် မြန်နှုန်းမြင့် WAL Mode သတ်မှတ်ခြင်း (Timeout 60s ထည့်ထားသည်) """
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        return conn
    except sqlite3.Error as e:
        log.error(f"❌ Database Connection Error: {e}")
        raise

def init_db():
    """ Database initialization နှင့် migration များကို safe ဖြစ်အောင် လုပ်ဆောင်ပေးသည် (Version 5.0) """
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        
        # ၁။ Core Tables
        c.execute('CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, branch TEXT, dept TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        c.execute('''CREATE TABLE IF NOT EXISTS os_groups (
                     chat_id INTEGER,
                     shop_name TEXT,
                     group_id INTEGER,
                     group_name TEXT,
                     invite_link TEXT,
                     topic_name TEXT,
                     topic_id INTEGER,
                     last_read_message_id INTEGER DEFAULT 0
                   )''')
        c.execute('''CREATE TABLE IF NOT EXISTS message_logs
                     (msg_id INTEGER, chat_id INTEGER, topic_id INTEGER, user_id INTEGER,
                      text TEXT, timestamp INTEGER, status TEXT DEFAULT 'PENDING', resolved_by TEXT, resolve_time INTEGER,
                      media_id TEXT, is_manual INTEGER DEFAULT 0,
                      category TEXT, intent TEXT,
                      PRIMARY KEY (msg_id, chat_id))''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS alert_tracking (
                     original_msg_id INTEGER,
                     chat_id INTEGER,
                     alert_msg_id INTEGER,
                     alert_chat_id INTEGER,
                     created_at INTEGER,
                     esc_msg_id INTEGER,
                     linked_msg_ids TEXT DEFAULT '[]',
                     linked_customer_ids TEXT DEFAULT '[]',
                     PRIMARY KEY (original_msg_id, chat_id)
                   )''')
        
        # Central Routing Table
        c.execute('''CREATE TABLE IF NOT EXISTS routing_table (
                     topic_id INTEGER PRIMARY KEY,
                     target_group_id INTEGER,
                     target_topic_id INTEGER
                   )''')

        # Feedback & AI Learning Tables
        c.execute('''CREATE TABLE IF NOT EXISTS feedback_logs (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     message_id INTEGER,
                     chat_id INTEGER,
                     topic_id INTEGER,
                     category TEXT,
                     original_text TEXT,
                     staff_id INTEGER,
                     timestamp INTEGER
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS master_rules (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     rule_content TEXT,
                     chat_id INTEGER,
                     topic_id INTEGER,
                     created_at INTEGER
                   )''')
        
        # Initialize Routing Data
        c.execute("INSERT OR IGNORE INTO routing_table (topic_id, target_group_id, target_topic_id) VALUES (?, ?, ?)", (37, -1003601049225, 37))
        c.execute("INSERT OR IGNORE INTO routing_table (topic_id, target_group_id, target_topic_id) VALUES (?, ?, ?)", (1, -1003601049225, 1))
        c.execute("INSERT OR IGNORE INTO routing_table (topic_id, target_group_id, target_topic_id) VALUES (?, ?, ?)", (35, -1003601049225, 35))

        # ၂။ Migration Engine (Column အသစ်များ အလိုအလျောက် စစ်ဆေးထည့်သွင်းခြင်း)
        migrations = {
            "staff": ["branch TEXT", "dept TEXT"],
            "message_logs": ["text TEXT", "resolved_by TEXT", "resolve_time INTEGER", "topic_id INTEGER", "media_id TEXT", "is_manual INTEGER DEFAULT 0", "category TEXT", "intent TEXT"],
            "os_groups": ["last_read_message_id INTEGER DEFAULT 0"],
            "alert_tracking": ["created_at INTEGER", "esc_msg_id INTEGER", "linked_msg_ids TEXT DEFAULT '[]'", "linked_customer_ids TEXT DEFAULT '[]'"],
            "feedback_logs": ["chat_id INTEGER", "topic_id INTEGER", "category TEXT", "original_text TEXT", "staff_id INTEGER"],
            "master_rules": ["chat_id INTEGER", "topic_id INTEGER", "rule_content TEXT"]
        }
        
        for table, columns in migrations.items():
            for col in columns:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

        # ၃။ Default Settings
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_active', 'True')")

        # ၄။ Performance Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_status_time ON message_logs(status, timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_chat_topic_status ON message_logs(chat_id, topic_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_os_groups_chat_topic ON os_groups(chat_id, topic_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_feedback_logs_chat_topic ON feedback_logs(chat_id, topic_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_master_rules_chat_topic ON master_rules(chat_id, topic_id)")

        conn.commit()
        log.info("✅ Database (Version 5.0) Initialized Successfully.")
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            log.warning("⚠️ Database is locked. Skipping migration.")
        else:
            log.error(f"❌ Database Init Error: {e}")
    finally:
        conn.close()

def update_last_read_id(chat_id, topic_id, last_id):
    """ Smart Polling အတွက် နောက်ဆုံးဖတ်ပြီးသား Message ID ကို မှတ်သားရန် """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE os_groups SET last_read_message_id = ? WHERE chat_id = ? AND topic_id = ?",
            (last_id, chat_id, topic_id)
        )
        conn.commit()
    finally:
        conn.close()

# --- [ Settings Helpers ] ---
def set_setting(key, value):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_setting(key, default='True'):
    conn = get_connection()
    res = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return res[0] if res else default

# --- [ Staff Management ] ---
def add_staff(user_id, name, branch="General", dept="General"):
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO staff VALUES (?, ?, ?, ?)", (int(user_id), name, branch, dept))
        conn.commit()
    finally: conn.close()

def remove_staff(user_id):
    conn = get_connection()
    conn.execute("DELETE FROM staff WHERE user_id=?", (int(user_id),))
    conn.commit()
    conn.close()

def get_all_staff():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM staff").fetchall()
    conn.close()
    return rows

def check_if_staff(user_id):
    """ user_id သည် ဝန်ထမ်းစာရင်းထဲတွင် ရှိ/မရှိ စစ်ဆေးခြင်း """
    if not user_id: return False
    conn = get_connection()
    res = conn.execute("SELECT 1 FROM staff WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return res is not None

def get_staff_info(user_id):
    conn = get_connection()
    res = conn.execute("SELECT user_id, name, branch, dept FROM staff WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return res

# --- [ Message & SLA Logging ] ---
def log_message(msg_id, chat_id, topic_id, user_id, text, timestamp=None, media_id=None):
    if timestamp is None: timestamp = int(time.time())
    # 💡 General Topic Fallback Patch
    safe_topic_id = topic_id if topic_id and topic_id != 0 else 1
    
    conn = get_connection()
    try:
        # 💡 Parameter ၇ ခု + 'PENDING' (စုစုပေါင်း ၈ ခု) ကို သေချာစွာ ထည့်သွင်းခြင်း
        conn.execute(
            "INSERT OR IGNORE INTO message_logs (msg_id, chat_id, topic_id, user_id, text, timestamp, status, media_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, chat_id, safe_topic_id, user_id, text, timestamp, 'PENDING', media_id)
        )
        rows_affected = conn.total_changes
        conn.commit()
        
        # Debug: စာမှတ်တမ်းတင်ခြင်း ရှိ/မရှိ စစ်ဆေး
        if rows_affected > 0:
            log.info(f"✓ Message {msg_id} in {chat_id} saved as PENDING")
        else:
            log.warning(f"⚠ Message {msg_id} in {chat_id} already exists or failed to save")
            # အကယ်။ message_logs ထဲမှာ ရှိနှင့်ပြီးသား status စစ်ဆေးပါမည်
            status_res = conn.execute("SELECT status FROM message_logs WHERE msg_id=? AND chat_id=?", (msg_id, chat_id)).fetchone()
            if status_res:
                log.info(f"  → Current status: {status_res[0]}")
    finally: conn.close()

def get_message_topic(msg_id, chat_id):
    """ Message တစ်ခု၏ topic_id ကို ရှာဖွေရန် helper """
    conn = get_connection()
    try:
        res = conn.execute("SELECT topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        return res[0] if res else 0
    finally:
        conn.close()

def is_manual_alert(msg_id, chat_id):
    """ Message သည် Manual Alert (/alert) ဖြစ်မဖြစ် စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        res = conn.execute("SELECT is_manual FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        return (res[0] == 1) if res else False
    finally:
        conn.close()

def set_manual_alert(msg_id, chat_id):
    """ Message ကို Manual Alert အဖြစ် သတ်မှတ်ခြင်း """
    conn = get_connection()
    try:
        conn.execute("UPDATE message_logs SET is_manual = 1 WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id))
        conn.commit()
    finally:
        conn.close()

def resolve_message(msg_id, chat_id, staff_name, method='Reply', topic_id=None):
    """
    Message တစ်ခု သို့မဟုတ် Topic တစ်ခုလုံးကို Resolved အဖြစ် သတ်မှတ်ခြင်း။
    topic_id ပါလာလျှင် ထို topic ထဲက pending များကိုသာ resolve လုပ်မည်။
    """
    conn = get_connection()
    now = int(time.time())
    full_staff_info = f"{staff_name} ({method})" if method else staff_name
    
    try:
        if msg_id != 0:
            # 💡 Linked Resolution Logic: Find if this message is part of a group
            import json
            # ၁။ Check if it's a parent (original_msg_id)
            res = conn.execute("SELECT linked_customer_ids FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
            
            # ၂။ Check if it's a child (in linked_customer_ids of some parent)
            if not res:
                res_child = conn.execute("SELECT original_msg_id, linked_customer_ids FROM alert_tracking WHERE chat_id = ? AND linked_customer_ids LIKE ?", (chat_id, f'%{msg_id}%')).fetchone()
                if res_child:
                    parent_id, linked_json = res_child
                    linked_ids = json.loads(linked_json)
                    if msg_id in linked_ids:
                        # If child is resolved, we treat the parent as the main ID to resolve the whole group
                        msg_id = parent_id
                        res = (linked_json,)

            # ၃။ Resolve the whole group if linked IDs found
            ids_to_resolve = [msg_id]
            if res and res[0]:
                try:
                    linked_ids = json.loads(res[0])
                    ids_to_resolve.extend(linked_ids)
                except: pass

            # 💡 Update all messages in the group
            placeholders = ', '.join(['?'] * len(ids_to_resolve))
            query = f"UPDATE message_logs SET status='RESOLVED', resolved_by=?, resolve_time=? WHERE msg_id IN ({placeholders}) AND chat_id=?"
            params = [full_staff_info, now] + ids_to_resolve + [chat_id]
            
            conn.execute(query, tuple(params))
            log.info(f"✅ Group Resolved: {ids_to_resolve} in {chat_id} by {full_staff_info}")
        else:
            # msg_id 0 ဖြစ်လျှင် chat_id (နှင့် topic_id ပါလျှင် topic_id) အလိုက် resolve လုပ်မည်
            query = "UPDATE message_logs SET status='RESOLVED', resolved_by=?, resolve_time=? WHERE chat_id=? AND status IN ('PENDING', 'ALERTED', 'ESCALATED')"
            params = [full_staff_info, now, chat_id]
            if topic_id is not None:
                query += " AND topic_id=?"
                params.append(topic_id)
            
            conn.execute(query, tuple(params))
            log.warning(f"⚠️ Pending messages in {chat_id} (Topic: {topic_id}) resolved by {staff_name}")
            
        conn.commit()
    finally: conn.close()

def auto_resolve_stale_alerts(hours=30):
    """
    သတ်မှတ်ထားသော နာရီထက်ကျော်နေသည့် ALERTED/ESCALATED စာများကို
    Record Group သို့ မပို့ဘဲ အလိုအလျောက် RESOLVED ပြောင်းပေးခြင်း။
    """
    conn = get_connection()
    now = int(time.time())
    threshold = now - (hours * 3600)
    
    try:
        # ALERTED သို့မဟုတ် ESCALATED ဖြစ်နေပြီး threshold ထက်ဟောင်းနေသောစာများကို ရှာမည်
        query = "UPDATE message_logs SET status='RESOLVED', resolved_by='System (Auto-Expired)', resolve_time=? WHERE status IN ('ALERTED', 'ESCALATED') AND timestamp < ?"
        res = conn.execute(query, (now, threshold))
        count = res.rowcount
        conn.commit()
        if count > 0:
            log.warning(f"🧹 Auto-resolved {count} stale alerts (Older than {hours} hours)")
        return count
    except Exception as e:
        log.error(f"❌ Auto-resolve stale alerts failed: {e}")
        return 0
    finally:
        conn.close()

# --- [ Watchdog & Context Helpers ] ---
def get_pending_topics(minutes=15, max_hours=48):
    """ ၁၅ မိနစ်ထက်ကျော်နေသော Pending စာရှိသည့် (chat_id, topic_id) စာရင်းကို ယူသည် """
    conn = get_connection()
    now = int(time.time())
    threshold = now - (minutes * 60)
    lookback_limit = now - (max_hours * 3600)
    
    res = conn.execute(
        """SELECT DISTINCT chat_id, topic_id
           FROM message_logs
           WHERE status='PENDING' AND timestamp < ? AND timestamp > ?""",
        (threshold, lookback_limit)
    ).fetchall()
    conn.close()
    return res

def get_pending_messages(minutes=15, limit=10, max_hours=48, chat_id=None, topic_id=None, all_pending=False):
    """
    ၁၅ မိနစ်ထက်ကျော်နေသော Pending စာများကို ဆွဲထုတ်ပေးသည် (Topic-specific filtering support)
    all_pending=True ဖြစ်ပါက ၁၅ မိနစ်မပြည့်သေးသော်လည်း Pending ဖြစ်နေသမျှ အကုန်ယူမည် (Batching အတွက်)
    """
    conn = get_connection()
    now = int(time.time())
    threshold = now - (minutes * 60)
    lookback_limit = now - (max_hours * 3600)
    
    if all_pending:
        query = "SELECT msg_id, chat_id, topic_id, text, timestamp, media_id FROM message_logs WHERE status='PENDING' AND timestamp > ?"
        params = [lookback_limit]
    else:
        query = "SELECT msg_id, chat_id, topic_id, text, timestamp, media_id FROM message_logs WHERE status='PENDING' AND timestamp < ? AND timestamp > ?"
        params = [threshold, lookback_limit]
    
    if chat_id is not None:
        query += " AND chat_id = ?"
        params.append(chat_id)
    if topic_id is not None:
        query += " AND topic_id = ?"
        params.append(topic_id)
        
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    
    res = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return res

def get_active_alerts_for_group(chat_id, topic_id):
    """ လက်ရှိ group/topic မှာ တက်နေဆဲဖြစ်သော Alert များကို ရှာပေးသည် """
    conn = get_connection()
    res = conn.execute(
        """SELECT a.alert_msg_id, a.alert_chat_id, m.text
           FROM alert_tracking a
           JOIN message_logs m ON a.original_msg_id = m.msg_id AND a.chat_id = m.chat_id
           WHERE a.chat_id = ? AND m.topic_id = ? AND m.status IN ('ALERTED', 'ESCALATED')
           LIMIT 5""",
        (chat_id, topic_id)
    ).fetchall()
    conn.close()
    return res

def get_messages_before(chat_id, topic_id, msg_id, limit=5):
    """ သတ်မှတ်ထားသော message အရှေ့က စာများကို ဆွဲထုတ်ပေးသည် """
    conn = get_connection()
    res = conn.execute(
        "SELECT text, user_id, timestamp, status FROM message_logs WHERE chat_id = ? AND topic_id = ? AND msg_id < ? ORDER BY msg_id DESC LIMIT ?",
        (chat_id, topic_id, msg_id, limit)
    ).fetchall()
    conn.close()
    # DESC နဲ့ ယူထားတာမို့လို့ အစဉ်လိုက်ဖြစ်အောင် reverse ပြန်လုပ်ပေးရမယ်
    return res[::-1]

def get_messages_after(chat_id, topic_id, msg_id, limit=5):
    """ သတ်မှတ်ထားသော message နောက်ပိုင်းဝင်လာသည့် စာများကို ဆွဲထုတ်ပေးသည် (ဝန်ထမ်းစာများအပါအဝင်) """
    conn = get_connection()
    res = conn.execute(
        "SELECT text, user_id, timestamp, status FROM message_logs WHERE chat_id = ? AND topic_id = ? AND msg_id > ? ORDER BY msg_id ASC LIMIT ?",
        (chat_id, topic_id, msg_id, limit)
    ).fetchall()
    conn.close()
    return res

def update_message_status(msg_id, chat_id, status, topic_id=None, category=None, intent=None):
    """ Message တစ်ခုချင်းစီ၏ Status ကို Update လုပ်ရန် (Category/Intent သိမ်းဆည်းမှု အပါအဝင်) """
    conn = get_connection()
    try:
        updates = ["status = ?"]
        params = [status]
        
        if category:
            updates.append("category = ?")
            params.append(category)
        if intent:
            updates.append("intent = ?")
            params.append(intent)
            
        query = f"UPDATE message_logs SET {', '.join(updates)} WHERE msg_id = ? AND chat_id = ?"
        params.extend([msg_id, chat_id])
        
        if topic_id is not None:
            query += " AND topic_id = ?"
            params.append(topic_id)
            
        conn.execute(query, tuple(params))
        conn.commit()
    finally:
        conn.close()

def get_topic_context(chat_id, topic_id):
    """ AI Analysis အတွက် မဖြေရသေးသောစာများနှင့် နောက်ဆုံးဖြေထားသောစာ ၅ ကြောင်းကို ယူသည် """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT msg_id, text, timestamp FROM message_logs WHERE chat_id=? AND topic_id=? AND status='PENDING' ORDER BY timestamp ASC", (chat_id, topic_id))
    pending = c.fetchall()
    c.execute("SELECT text FROM message_logs WHERE chat_id=? AND topic_id=? AND status='RESOLVED' ORDER BY timestamp DESC LIMIT 5", (chat_id, topic_id))
    resolved = [r[0] for r in c.fetchall()]
    c.execute("SELECT shop_name FROM os_groups WHERE chat_id=? LIMIT 1", (chat_id,))
    g_res = c.fetchone()
    shop_name = clean_shop_name(g_res[0]) if g_res else "Unknown Shop"
    conn.close()
    return pending, resolved, shop_name

# --- [ Alert Tracking Helpers ] ---
def save_alert_tracking(original_msg_id, chat_id, alert_msg_id, alert_chat_id):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO alert_tracking (original_msg_id, chat_id, alert_msg_id, alert_chat_id, created_at, linked_msg_ids) VALUES (?, ?, ?, ?, ?, '[]')",
            (original_msg_id, chat_id, alert_msg_id, alert_chat_id, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()

def update_alert_tracking_esc(original_msg_id, chat_id, esc_msg_id):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE alert_tracking SET esc_msg_id = ? WHERE original_msg_id = ? AND chat_id = ?",
            (esc_msg_id, original_msg_id, chat_id)
        )
        conn.commit()
    finally:
        conn.close()

def add_linked_msg_id(original_msg_id, chat_id, new_msg_id):
    conn = get_connection()
    try:
        res = conn.execute("SELECT linked_msg_ids FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?", (original_msg_id, chat_id)).fetchone()
        if res:
            import json
            ids = json.loads(res[0])
            ids.append(new_msg_id)
            conn.execute("UPDATE alert_tracking SET linked_msg_ids = ? WHERE original_msg_id = ? AND chat_id = ?", (json.dumps(ids), original_msg_id, chat_id))
            conn.commit()
    finally:
        conn.close()

def get_alert_tracking(original_msg_id, chat_id):
    conn = get_connection()
    res = conn.execute(
        "SELECT alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?",
        (original_msg_id, chat_id)
    ).fetchone()
    conn.close()
    return res

def get_parent_msg_id(msg_id, chat_id):
    """ Child Message ID မှ Parent (Original) Message ID ကို ရှာပေးခြင်း """
    conn = get_connection()
    try:
        # ၁။ သူကိုယ်တိုင်က Parent ဖြစ်နေသလား အရင်စစ်
        res = conn.execute("SELECT original_msg_id FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        if res: return msg_id
        
        # ၂။ Child ဖြစ်နေသလား စစ် (linked_customer_ids ထဲမှာ ပါနေသလား)
        res_child = conn.execute("SELECT original_msg_id FROM alert_tracking WHERE chat_id = ? AND linked_customer_ids LIKE ?", (chat_id, f'%{msg_id}%')).fetchone()
        if res_child:
            return res_child[0]
        
        return msg_id # ရှာမတွေ့ရင် သူ့ကိုယ်သူပဲ ပြန်ပေး
    finally:
        conn.close()

def get_original_msg_id_by_alert(alert_msg_id, alert_chat_id):
    """ Alert Message ID မှ မူရင်း Customer Message ID ကို ရှာပေးခြင်း """
    conn = get_connection()
    try:
        res = conn.execute("SELECT original_msg_id FROM alert_tracking WHERE alert_msg_id = ? AND alert_chat_id = ?", (alert_msg_id, alert_chat_id)).fetchone()
        return res[0] if res else None
    finally:
        conn.close()

def add_linked_customer_id(original_msg_id, chat_id, customer_msg_id):
    """ Grouped Ticket အတွက် Customer Message ID များကို ချိတ်ဆက်သိမ်းဆည်းခြင်း """
    conn = get_connection()
    try:
        res = conn.execute("SELECT linked_customer_ids FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?", (original_msg_id, chat_id)).fetchone()
        if res:
            import json
            ids = json.loads(res[0]) if res[0] else []
            if customer_msg_id not in ids:
                ids.append(customer_msg_id)
                conn.execute("UPDATE alert_tracking SET linked_customer_ids = ? WHERE original_msg_id = ? AND chat_id = ?", (json.dumps(ids), original_msg_id, chat_id))
                conn.commit()
    finally:
        conn.close()

def delete_alert_tracking(original_msg_id, chat_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?", (original_msg_id, chat_id))
        conn.commit()
    finally:
        conn.close()

def get_routing_entry(chat_id, topic_id):
    """
    OS Group ၏ topic အလိုက် alert ပို့ရမည့် group ကို ရှာဖွေခြင်း။
    လက်ရှိတွင် os_groups table ထဲမှ group_id ကို သုံးထားသည်။
    """
    conn = get_connection()
    res = conn.execute(
        "SELECT group_id, topic_id FROM os_groups WHERE chat_id = ? AND topic_id = ?",
        (chat_id, topic_id)
    ).fetchone()
    conn.close()
    return res

# --- [ OS Group & Analytics ] ---
def check_if_os_group(chat_id):
    conn = get_connection()
    res = conn.execute("SELECT 1 FROM os_groups WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return res is not None

def add_os_group(chat_id, shop_name):
    clean_name = clean_shop_name(shop_name)
    conn = get_connection()
    conn.execute(
        "INSERT INTO os_groups (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, clean_name, chat_id, clean_name, "Legacy", "Legacy", 0)
    )
    conn.commit()
    conn.close()

def save_manual_register(chat_id, shop_name, topic_entries):
    """
    OS Group များကို Topic အလိုက် မှတ်ပုံတင်ခြင်း (Routing Table မပါတော့ပါ)
    topic_entries = [
      {"topic_name": "...", "topic_id": 12},
      ...
    ]
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        clean_name = clean_shop_name(shop_name)
        for item in topic_entries:
            t_name = item["topic_name"]
            t_id = int(item["topic_id"])

            c.execute(
                """INSERT INTO os_groups
                   (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, clean_name, chat_id, clean_name, "Manual Register", t_name, t_id)
            )
        conn.commit()
    finally:
        conn.close()

def get_os_group_names():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT shop_name FROM os_groups").fetchall()
        return [(clean_shop_name(name),) for (name,) in rows]
    finally:
        conn.close()

def delete_os_group_by_chat_id(chat_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM os_groups WHERE chat_id=?", (chat_id,))
        conn.commit()
    finally:
        conn.close()

def get_pending_counts_by_shop():
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT o.shop_name, COUNT(m.msg_id) 
            FROM message_logs m
            JOIN os_groups o ON m.chat_id = o.chat_id
            WHERE m.status='PENDING'
            GROUP BY m.chat_id
            """
        ).fetchall()
    finally:
        conn.close()

def find_os_groups_by_keyword(keyword):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT chat_id, shop_name FROM os_groups WHERE shop_name LIKE ?",
            (f'%{keyword}%',)
        ).fetchall()
        return [(chat_id, clean_shop_name(shop_name)) for chat_id, shop_name in rows]
    finally:
        conn.close()

def get_staff_stats(period="all"):
    conn = get_connection()
    query = "SELECT resolved_by, COUNT(*), AVG((resolve_time - timestamp)/60.0) FROM message_logs WHERE status = 'RESOLVED' AND resolved_by IS NOT NULL "
    
    now = datetime.now()
    if period == "today":
        start_ts = int(now.replace(hour=0, minute=0, second=0).timestamp())
        query += f"AND resolve_time >= {start_ts} "
    elif period == "weekly":
        # လွန်ခဲ့သော ၇ ရက်
        start_ts = int((now - timedelta(days=7)).timestamp())
        query += f"AND resolve_time >= {start_ts} "
    elif period == "month":
        start_ts = int(now.replace(day=1, hour=0, minute=0, second=0).timestamp())
        query += f"AND resolve_time >= {start_ts} "
        
    query += "GROUP BY resolved_by ORDER BY COUNT(*) DESC"
    res = conn.execute(query).fetchall()
    conn.close()
    return res

# --- [ Feedback & AI Learning Helpers ] ---
def save_feedback(message_id, chat_id, topic_id, category, original_text, staff_id):
    """ AI Feedback ကို သိမ်းဆည်းခြင်း (Isolated by chat & topic) """
    safe_topic_id = topic_id if topic_id and topic_id != 0 else 1
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO feedback_logs (message_id, chat_id, topic_id, category, original_text, staff_id, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, chat_id, safe_topic_id, category, original_text, staff_id, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()

def get_isolated_feedback(chat_id, topic_id, limit=10):
    """ သတ်မှတ်ထားသော chat/topic အတွက် နောက်ဆုံး feedback များကို ယူခြင်း """
    safe_topic_id = topic_id if topic_id and topic_id != 0 else 1
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT category, original_text FROM feedback_logs WHERE chat_id = ? AND topic_id = ? ORDER BY timestamp DESC LIMIT ?",
            (chat_id, safe_topic_id, limit)
        ).fetchall()
        return res
    finally:
        conn.close()

def save_master_rule(rule_content, chat_id, topic_id):
    """ AI မှ ထုတ်ပေးသော Rule ကို သိမ်းဆည်းခြင်း """
    safe_topic_id = topic_id if topic_id and topic_id != 0 else 1
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO master_rules (rule_content, chat_id, topic_id, created_at) VALUES (?, ?, ?, ?)",
            (rule_content, chat_id, safe_topic_id, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()

def get_isolated_rules(chat_id, topic_id):
    """ သတ်မှတ်ထားသော chat/topic အတွက် AI Rules များကို ယူခြင်း """
    safe_topic_id = topic_id if topic_id and topic_id != 0 else 1
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT rule_content FROM master_rules WHERE chat_id = ? AND topic_id = ? ORDER BY created_at DESC",
            (chat_id, safe_topic_id)
        ).fetchall()
        return [r[0] for r in res]
    finally:
        conn.close()

def clear_processed_feedback(chat_id, topic_id, before_timestamp):
    """ Summarize ပြီးသွားသော feedback များကို ဖျက်ခြင်း """
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM feedback_logs WHERE chat_id = ? AND topic_id = ? AND timestamp <= ?",
            (chat_id, topic_id, before_timestamp)
        )
        conn.commit()
    finally:
        conn.close()

# 💡 Worker များ၏ main script မှသာ init_db() ကို ခေါ်ရန် အကြံပြုသည်
# if __name__ == "__main__":
#     init_db()
