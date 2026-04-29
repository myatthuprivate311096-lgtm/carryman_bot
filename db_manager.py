# Version: 4.0 (2-Worker Architecture Ready)
import sqlite3
import time
import os
import pytz
from datetime import datetime
from contextlib import contextmanager
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
    Shop name မှ emoji/noise များကိုဖြတ်၍ base name ပြန်ပေးမည်။ (Burmese Support)
    🤝 ပါဝင်ပါက ၎င်း၏ ရှေ့က အမည်ကိုသာ ယူမည်။
    (***) ပါဝင်ပါက ၎င်းကို ဖယ်ရှားမည်။
    """
    if raw_name is None:
        return "Unknown Shop"
    
    raw_str = str(raw_name)
    
    # 🤝 ဖြင့် ခွဲထုတ်ခြင်း
    if '🤝' in raw_str:
        base = raw_str.split('🤝')[0]
    else:
        base = raw_str

    # (***) ဖြင့် ရေးထားသည်များကို ဖယ်ရှားခြင်း
    import re
    base = re.sub(r'\(.*?\)', '', base)
        
    # Emoji များကို ဖယ်ရှားခြင်း (Burmese characters များကို ချန်ထားမည်)
    # Unicode range for Burmese: \u1000-\u109F
    # We keep alphanumeric, spaces, and Burmese range.
    cleaned_base = re.sub(r'[^\w\s\u1000-\u109F]', '', base)
    
    # ပိုနေသော space များကို ရှင်းလင်းခြင်း
    cleaned = " ".join(cleaned_base.split()).strip()
    
    return cleaned or "Unknown Shop"

def get_connection():
    """ Database ချိတ်ဆက်မှုအား Multithreading နှင့် မြန်နှုန်းမြင့် WAL Mode သတ်မှတ်ခြင်း (Timeout 60s ထည့်ထားသည်) """
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA cache_size=-64000;') # 64MB Cache
        conn.execute('PRAGMA temp_store=MEMORY;')
        conn.execute('PRAGMA mmap_size=268435456;') # 256MB Mmap
        return conn
    except sqlite3.Error as e:
        log.error(f"❌ Database Connection Error: {e}")
        raise

@contextmanager
def connection_scope():
    """ Database connection ကို context manager အဖြစ် အသုံးပြုရန် (Auto-commit/rollback ပါဝင်သည်) """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    """ Database initialization နှင့် migration များကို safe ဖြစ်အောင် လုပ်ဆောင်ပေးသည် (Version 5.0) """
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        
        # ၁။ Core Tables
        c.execute('CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, branch TEXT, dept TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        c.execute('''CREATE TABLE IF NOT EXISTS functions_registry (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT UNIQUE,
                     description TEXT,
                     module_path TEXT
                   )''')
        c.execute('''CREATE TABLE IF NOT EXISTS os_groups (
                     chat_id INTEGER,
                     shop_name TEXT,
                     group_id INTEGER,
                     group_name TEXT,
                     invite_link TEXT,
                     topic_name TEXT,
                     topic_id INTEGER,
                     last_read_message_id INTEGER DEFAULT 0,
                     target_chat_id INTEGER,
                     target_topic_id INTEGER
                   )''')
        c.execute('''CREATE TABLE IF NOT EXISTS message_logs
                     (msg_id INTEGER, chat_id INTEGER, topic_id INTEGER, user_id INTEGER,
                      text TEXT, timestamp INTEGER, status TEXT DEFAULT 'PENDING', resolved_by TEXT, resolve_time INTEGER,
                      media_id TEXT, is_manual INTEGER DEFAULT 0,
                      category TEXT, intent TEXT, summary TEXT,
                      PRIMARY KEY (msg_id, chat_id))''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS alert_tracking (
                     original_msg_id INTEGER,
                     chat_id INTEGER,
                     alert_msg_id INTEGER,
                     alert_chat_id INTEGER,
                     created_at INTEGER,
                     esc_msg_id INTEGER,
                     esc_tier2_msg_id INTEGER,
                     linked_msg_ids TEXT DEFAULT '[]',
                     linked_customer_ids TEXT DEFAULT '[]',
                     PRIMARY KEY (original_msg_id, chat_id)
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS pickup_queue (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     chat_id INTEGER,
                     orig_msg_id INTEGER,
                     target_date TEXT,
                     os_name TEXT,
                     remark TEXT,
                     vehicle TEXT,
                     status TEXT DEFAULT 'PENDING',
                     error_msg TEXT,
                     created_at INTEGER
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS shop_mappings (
                     chat_id INTEGER PRIMARY KEY,
                     website_os_name TEXT,
                     updated_at INTEGER
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS website_shops (
                     name TEXT PRIMARY KEY,
                     created_at INTEGER
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT,
                        question TEXT,
                        answer TEXT,
                        tags TEXT,
                        level INTEGER DEFAULT 1,
                        last_updated INTEGER
                    )''')

        # Feedback & AI Learning Tables
        c.execute('''CREATE TABLE IF NOT EXISTS pickup_intermediate_messages (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     orig_msg_id INTEGER,
                     chat_id INTEGER,
                     intermediate_msg_id INTEGER,
                     created_at INTEGER
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS temp_data (
                     id TEXT PRIMARY KEY,
                     value TEXT,
                     created_at INTEGER
                   )''')

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

        c.execute('''CREATE TABLE IF NOT EXISTS temp_data (
                     id TEXT PRIMARY KEY,
                     value TEXT,
                     created_at INTEGER
                   )''')
        
        # ၂။ Migration Engine (Column အသစ်များ အလိုအလျောက် စစ်ဆေးထည့်သွင်းခြင်း)
        migrations = {
            "staff": ["branch TEXT", "dept TEXT"],
            "message_logs": ["text TEXT", "resolved_by TEXT", "resolve_time INTEGER", "topic_id INTEGER", "media_id TEXT", "is_manual INTEGER DEFAULT 0", "category TEXT", "intent TEXT", "summary TEXT"],
            "os_groups": ["last_read_message_id INTEGER DEFAULT 0", "target_chat_id INTEGER", "target_topic_id INTEGER"],
            "alert_tracking": ["created_at INTEGER", "esc_msg_id INTEGER", "esc_tier2_msg_id INTEGER", "linked_msg_ids TEXT DEFAULT '[]'", "linked_customer_ids TEXT DEFAULT '[]'"],
            "feedback_logs": ["chat_id INTEGER", "topic_id INTEGER", "category TEXT", "original_text TEXT", "staff_id INTEGER"],
            "master_rules": ["chat_id INTEGER", "topic_id INTEGER", "rule_content TEXT"],
            "knowledge_base": ["category TEXT", "question TEXT", "answer TEXT", "tags TEXT", "level INTEGER DEFAULT 1", "last_updated INTEGER"]
        }
        
        for table, columns in migrations.items():
            for col in columns:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

        # ၃။ Default Settings
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_active', 'True')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('env_mode', 'Sandbox')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('global_ai_answer', 'ON')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('global_auto_pickup', 'ON')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('global_alert_system', 'ON')")

        # ၄။ New Tables (Version 4.0)
        c.execute('''CREATE TABLE IF NOT EXISTS group_settings (
                     chat_id INTEGER PRIMARY KEY,
                     ai_status TEXT DEFAULT 'ON'
                   )''')

        # ၅။ User States (Private Chat Escalation)
        c.execute('''CREATE TABLE IF NOT EXISTS user_states (
                     user_id INTEGER PRIMARY KEY,
                     out_of_scope_count INTEGER DEFAULT 0,
                     human_intervention_needed INTEGER DEFAULT 0,
                     last_updated INTEGER
                   )''')

        # ၆။ Performance Indexes
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

# --- [ RBAC & User Levels ] ---
def get_user_level(user_id, chat_id):
    """
    User Level ခွဲခြားခြင်း (Level 1 to 4)
    Level 4 (Manager): .env ထဲက MANAGER_IDS ထဲမှာ ပါဝင်သူများ
    Level 3 (Staff): staff table ထဲမှာ ရှိနေသူများ
    Level 2 (OS): Group ထဲမှာ မေးမြန်းတဲ့ သာမန်အသုံးပြုသူများ (chat_id < 0)
    Level 1 (Customer): Private DM မှ လာမေးသူများ (chat_id > 0)
    """
    if not user_id: return 1
    
    # ၁။ Level 4: Manager Check
    manager_ids_raw = os.getenv('MANAGER_IDS', os.getenv('MANAGER_ID', ''))
    manager_ids = [int(i.strip()) for i in manager_ids_raw.split(',') if i.strip()]
    if user_id in manager_ids:
        return 4
        
    # ၂။ Level 3: Staff Check
    if check_if_staff(user_id):
        return 3
        
    # ၃။ Level 2 & 1: Group vs Private Check
    # Telegram တွင် Group chat_id များသည် အနှုတ် (-) ဖြင့် စတင်ပါသည်
    if chat_id and int(chat_id) < 0:
        return 2
    else:
        return 1

# --- [ Functions Registry ] ---
def add_function(name, description, module_path):
    """ Module အသစ်များကို functions_registry table ထဲသို့ ထည့်သွင်းခြင်း သို့မဟုတ် Update လုပ်ခြင်း """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO functions_registry (name, description, module_path) VALUES (?, ?, ?)",
            (name, description, module_path)
        )
        conn.commit()
        log.info(f"✅ Function Registered: {name} -> {module_path}")
        return True
    except Exception as e:
        log.error(f"❌ Failed to add function {name}: {e}")
        return False
    finally:
        conn.close()

# --- [ Message & SLA Logging ] ---
def get_mm_now():
    """ Asia/Yangon timezone ဖြင့် လက်ရှိအချိန်ကို ရယူရန် """
    tz = pytz.timezone('Asia/Yangon')
    return datetime.now(tz)

def get_mm_timestamp():
    """ Asia/Yangon timezone ဖြင့် လက်ရှိ timestamp ကို ရယူရန် """
    return int(get_mm_now().timestamp())

def log_message(msg_id, chat_id, topic_id, user_id, text, timestamp=None, media_id=None):
    if timestamp is None:
        timestamp = get_mm_timestamp()
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

def get_message_context(msg_id, chat_id):
    """ Message တစ်ခု၏ AI Context (summary, category, intent) ကို ပြန်ယူခြင်း """
    conn = get_connection()
    try:
        res = conn.execute("SELECT text, summary, category, intent, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        return res
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

def resolve_message(msg_id, chat_id, staff_name, method='Reply', topic_id=None, status='RESOLVED'):
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
            query = f"UPDATE message_logs SET status=?, resolved_by=?, resolve_time=? WHERE msg_id IN ({placeholders}) AND chat_id=?"
            params = [status, full_staff_info, now] + ids_to_resolve + [chat_id]
            
            conn.execute(query, tuple(params))
            log.info(f"✅ Group Resolved: {ids_to_resolve} in {chat_id} by {full_staff_info}")
        else:
            # msg_id 0 ဖြစ်လျှင် chat_id (နှင့် topic_id ပါလျှင် topic_id) အလိုက် resolve လုပ်မည်
            query = "UPDATE message_logs SET status=?, resolved_by=?, resolve_time=? WHERE chat_id=? AND status IN ('PENDING', 'ALERTED', 'ESCALATED')"
            params = [status, full_staff_info, now, chat_id]
            if topic_id is not None:
                query += " AND topic_id=?"
                params.append(topic_id)
            
            conn.execute(query, tuple(params))
            log.warning(f"⚠️ Pending messages in {chat_id} (Topic: {topic_id}) resolved by {staff_name}")
            
        conn.commit()
    finally: conn.close()

def cancel_message(msg_id, chat_id, reason='Cancelled by User'):
    """ Message ကို CANCELLED အဖြစ် သတ်မှတ်ခြင်း """
    conn = get_connection()
    now = int(time.time())
    try:
        conn.execute(
            "UPDATE message_logs SET status='CANCELLED', resolved_by=?, resolve_time=? WHERE msg_id=? AND chat_id=?",
            (reason, now, msg_id, chat_id)
        )
        conn.commit()
        log.info(f"❌ Message {msg_id} in {chat_id} cancelled. Reason: {reason}")
    except Exception as e:
        log.error(f"❌ Cancel Message Error: {e}")
    finally:
        conn.close()

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
           WHERE status='PENDING' AND status != 'HANDLED_BY_AI' AND timestamp < ? AND timestamp > ?""",
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
        query = "SELECT msg_id, chat_id, topic_id, text, timestamp, media_id FROM message_logs WHERE status='PENDING' AND status != 'HANDLED_BY_AI' AND timestamp > ?"
        params = [lookback_limit]
    else:
        query = "SELECT msg_id, chat_id, topic_id, text, timestamp, media_id FROM message_logs WHERE status='PENDING' AND status != 'HANDLED_BY_AI' AND timestamp < ? AND timestamp > ?"
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

def update_message_status(msg_id, chat_id, status, topic_id=None, category=None, intent=None, summary=None, text=None):
    """ Message တစ်ခုချင်းစီ၏ Status ကို Update လုပ်ရန် (Category/Intent/Summary/Text သိမ်းဆည်းမှု အပါအဝင်) """
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
        if summary:
            updates.append("summary = ?")
            params.append(summary)
        if text:
            updates.append("text = ?")
            params.append(text)
            
        query = f"UPDATE message_logs SET {', '.join(updates)} WHERE msg_id = ? AND chat_id = ?"
        params.extend([msg_id, chat_id])
        
        if topic_id is not None:
            query += " AND topic_id = ?"
            params.append(topic_id)
            
        conn.execute(query, tuple(params))
        conn.commit()
    except Exception as e:
        log.error(f"❌ update_message_status failed for msg_id={msg_id}, chat_id={chat_id}: {e}")
        raise
    finally:
        conn.close()

def get_topic_context(chat_id, topic_id):
    """ AI Analysis အတွက် မဖြေရသေးသောစာများနှင့် နောက်ဆုံးဖြေထားသောစာ ၅ ကြောင်းကို ယူသည် """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT msg_id, text, timestamp FROM message_logs WHERE chat_id=? AND topic_id=? AND status='PENDING' ORDER BY timestamp ASC", (chat_id, topic_id))
    pending = c.fetchall()
    c.execute("SELECT text FROM message_logs WHERE chat_id=? AND topic_id=? AND status IN ('RESOLVED', 'HANDLED_BY_AI') ORDER BY timestamp DESC LIMIT 5", (chat_id, topic_id))
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

def update_alert_tracking_esc(original_msg_id, chat_id, esc_msg_id, tier=1):
    conn = get_connection()
    try:
        column = "esc_msg_id" if tier == 1 else "esc_tier2_msg_id"
        conn.execute(
            f"UPDATE alert_tracking SET {column} = ? WHERE original_msg_id = ? AND chat_id = ?",
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
        "SELECT alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids, esc_tier2_msg_id FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?",
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
    OS Group ၏ topic အလိုက် alert ပို့ရမည့် group နှင့် topic ကို ရှာဖွေခြင်း။
    Type casting ထည့်သွင်းထားသဖြင့် String/Integer ကွဲလွဲမှုများကို ဖြေရှင်းပေးသည်။
    """
    try:
        # ၁။ Type Casting (String ဖြစ်နေပါက Integer သို့ ပြောင်းခြင်း)
        c_id = int(chat_id)
        t_id = int(topic_id) if topic_id is not None else 0
        
        conn = get_connection()
        res = conn.execute(
            "SELECT target_chat_id, target_topic_id FROM os_groups WHERE chat_id = ? AND topic_id = ?",
            (c_id, t_id)
        ).fetchone()
        conn.close()
        
        return res
    except Exception as e:
        log.error(f"❌ Error in get_routing_entry: {e}")
        return None

def update_routing_entry(chat_id, topic_id, target_chat_id, target_topic_id):
    """
    OS Group ၏ topic တစ်ခုအတွက် alert ပို့ရမည့် target ကို update လုပ်ခြင်း။
    မရှိသေးပါက အသစ်ထည့်သွင်းမည်။
    """
    conn = get_connection()
    try:
        # ၁။ အရင်ရှိမရှိ စစ်ဆေးခြင်း (သို့မဟုတ် INSERT OR REPLACE သုံးခြင်း)
        # os_groups မှာ chat_id, topic_id က primary key သို့မဟုတ် unique ဖြစ်ရမည်
        # လက်ရှိ schema အရ UPDATE အရင်လုပ်ကြည့်ပြီး rowcount 0 ဖြစ်လျှင် INSERT လုပ်မည်
        cursor = conn.execute(
            "UPDATE os_groups SET target_chat_id = ?, target_topic_id = ? WHERE chat_id = ? AND topic_id = ?",
            (target_chat_id, target_topic_id, chat_id, topic_id)
        )
        
        if cursor.rowcount == 0:
            # Row မရှိသေးပါက အသစ်ထည့်မည် (shop_name ကို topic_context မှ ယူမည်)
            _, _, shop_name = get_topic_context(chat_id, topic_id)
            conn.execute(
                "INSERT INTO os_groups (chat_id, topic_id, shop_name, target_chat_id, target_topic_id) VALUES (?, ?, ?, ?, ?)",
                (chat_id, topic_id, shop_name, target_chat_id, target_topic_id)
            )
            log.info(f"🆕 New Routing Created: {chat_id}/{topic_id} ({shop_name}) -> {target_chat_id}/{target_topic_id}")
        else:
            log.info(f"✅ Routing Updated: {chat_id}/{topic_id} -> {target_chat_id}/{target_topic_id}")
            
        conn.commit()
    except Exception as e:
        log.error(f"❌ Failed to update routing entry: {e}")
    finally:
        conn.close()

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
        "INSERT INTO os_groups (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id, target_chat_id, target_topic_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, clean_name, chat_id, clean_name, "Legacy", "Legacy", 0, -1003601049225, 1)
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

            # Default routing based on name if not provided
            target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
            target_topic = 1
            t_name_lower = t_name.lower()
            
            # Logic: နာမည်အလိုက် Target Topic သတ်မှတ်ခြင်း
            if any(x in t_name_lower for x in ["error", "ပို့မရ"]):
                target_topic = 37
            elif any(x in t_name_lower for x in ["fin", "voc", "ငွေစာရင်း", "ဘောင်ချာ"]):
                target_topic = 35
            elif any(x in t_name_lower for x in ["pick up", "urgent", "စုံစမ်းရန်"]):
                target_topic = 1

            c.execute(
                """INSERT INTO os_groups
                   (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id, target_chat_id, target_topic_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, clean_name, chat_id, clean_name, "Manual Register", t_name, t_id, target_chat, target_topic)
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
            WHERE m.status='PENDING' AND m.status != 'HANDLED_BY_AI'
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
    
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    if period == "today":
        # လွန်ခဲ့သော ၂၄ နာရီ
        start_ts = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        query += f"AND resolve_time >= {start_ts} "
    elif period == "weekly":
        # လွန်ခဲ့သော ၇ ရက်
        start_ts = int((now - timedelta(days=7)).timestamp())
        query += f"AND resolve_time >= {start_ts} "
    elif period == "month":
        start_ts = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
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

def upsert_knowledge_batch(data_list):
    """
    Google Sheet မှလာသော Data စာရင်းကို တစ်ခါတည်း အကုန်သွင်းပေးခြင်း။
    data_list: [(category, question, answer, tags, level, timestamp), ...]
    """
    conn = get_connection()
    try:
        # Sync လုပ်တိုင်း အဟောင်းတွေကို ဖျက်ပြီး အသစ်ပြန်သွင်းရန် (Data ထပ်မနေစေရန်)
        conn.execute("DELETE FROM knowledge_base")
        conn.executemany(
            "INSERT INTO knowledge_base (category, question, answer, tags, level, last_updated) VALUES (?, ?, ?, ?, ?, ?)",
            data_list
        )
        conn.commit()
        log.info(f"✅ Knowledge Base Sync: {len(data_list)} rows updated.")
        return True
    except Exception as e:
        log.error(f"❌ Batch Upsert Error: {e}")
        return False
    finally:
        conn.close()

def search_knowledge(query, user_level):
    """
    User ရဲ့ Level အလိုက် Database ထဲမှာ မေးခွန်းရှာပေးခြင်း
    Level 4: 1,2,3,4 အကုန်ရမည်
    Level 3: 1,2,3 ရမည်
    Level 2: 1,2 ရမည်
    Level 1: 1 ပဲရမည်
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        search_query = f"%{query}%"
        # 💡 Broadened Search Logic: Search across question, answer, tags, and category
        # This ensures Myanmar keywords match even if the question is in English but answer contains Myanmar.
        # 💡 Broadened Search Logic: Search across question, answer, tags, and category
        # Increased limit to 10 to provide more context for reasoning.
        c.execute('''SELECT category, question, answer FROM knowledge_base
                     WHERE (question LIKE ? OR answer LIKE ? OR tags LIKE ? OR category LIKE ?)
                     AND level <= ?
                     ORDER BY level DESC LIMIT 10''',
                     (search_query, search_query, search_query, search_query, user_level))
        
        results = c.fetchall()
        if results:
            # Combine multiple results for better context
            combined_context = ""
            for cat, q, a in results:
                combined_context += f"Category: {cat}\nQuestion: {q}\nAnswer: {a}\n---\n"
            return combined_context
        return None
    except Exception as e:
        log.error(f"❌ Search Knowledge Error: {e}")
        return None
    finally:
        conn.close()

def get_core_policies():
    """
    Fetch core company policies, terms, and conditions dynamically from the database.
    This data is injected into the AI's base context.
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        # Fetch categories that represent global rules and policies
        # We use a broader search to ensure we catch all policy-related data
        categories = [
            'Returns & Liability (တာဝန်ယူမှု)',
            'General Info (အထွေထွေ)',
            'ပို့ဆောင်ရေး ဝန်ဆောင်မှု (Delivery Service)',
            'Delivery Service (ပို့ဆောင်ရေး)',
            'Terms_Condition_Myanmar'
        ]
        
        placeholders = ', '.join(['?'] * len(categories))
        # Also search for keywords in category names to be safe
        query = f"""
            SELECT category, question, answer FROM knowledge_base
            WHERE category IN ({placeholders})
            OR category LIKE '%Terms%'
            OR category LIKE '%Condition%'
            OR category LIKE '%Policy%'
            OR tags LIKE '%policy%'
            OR tags LIKE '%rule%'
        """
        
        c.execute(query, tuple(categories))
        rows = c.fetchall()
        
        if not rows:
            return "No core policies found in database."
            
        policies_text = "[DYNAMIC CORE POLICIES & TERMS]\n"
        for cat, q, a in rows:
            policies_text += f"Category: {cat}\nQuestion: {q}\nAnswer: {a}\n---\n"
            
        return policies_text
    except Exception as e:
        log.error(f"❌ Error fetching core policies: {e}")
        return "Error loading core policies."
    finally:
        conn.close()

# --- [ Pickup Queue Helpers ] ---
def add_to_pickup_queue(chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status='PENDING'):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO pickup_queue (chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, int(time.time()))
        )
        last_id = cursor.lastrowid
        conn.commit()
        return last_id
    finally:
        conn.close()

def confirm_pickup_order(queue_id):
    """ WAITING_CONFIRM ဖြစ်နေသော order ကို PENDING ပြောင်း၍ စက်ရုပ်ကို အလုပ်လုပ်ခိုင်းခြင်း """
    conn = get_connection()
    try:
        conn.execute("UPDATE pickup_queue SET status = 'PENDING' WHERE id = ?", (queue_id,))
        conn.commit()
    finally:
        conn.close()

def delete_pickup_order(queue_id):
    """ Queue ထဲမှ order ကို ဖျက်ခြင်း (ပြင်ဆင်ရန် သို့မဟုတ် Admin နှင့်ပြောရန်) """
    conn = get_connection()
    try:
        conn.execute("DELETE FROM pickup_queue WHERE id = ?", (queue_id,))
        conn.commit()
    finally:
        conn.close()

def get_next_queued_pickup():
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle FROM pickup_queue WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        return res
    finally:
        conn.close()

def update_queue_status(queue_id, status, error_msg=None):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pickup_queue SET status = ?, error_msg = ? WHERE id = ?",
            (status, error_msg, queue_id)
        )
        conn.commit()
    finally:
        conn.close()

def retry_failed_pickups(chat_id):
    """ Mapping ပြင်ပြီးနောက် FAILED ဖြစ်နေသော pickup များကို PENDING ပြန်ပြောင်း၍ Retry လုပ်ခြင်း """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pickup_queue SET status = 'PENDING', error_msg = NULL WHERE chat_id = ? AND status = 'FAILED'",
            (chat_id,)
        )
        conn.commit()
        log.info(f"🔄 Retrying failed pickups for chat_id: {chat_id}")
    finally:
        conn.close()


# --- [ Pickup Duplicate Check ] ---
def check_existing_pickup(chat_id, target_date):
    """ သတ်မှတ်ထားတဲ့ ရက်စွဲနဲ့ ဆိုင် (Chat ID) အတွက် အောင်မြင်ပြီးသား Pickup ရှိမရှိ စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT 1 FROM pickup_queue WHERE chat_id = ? AND target_date = ? AND status = 'SUCCESS'",
            (chat_id, target_date)
        ).fetchone()
        return res is not None
    finally:
        conn.close()

# --- [ Shop Mapping Helpers ] ---
def get_shop_mapping(chat_id):
    conn = get_connection()
    try:
        res = conn.execute("SELECT website_os_name FROM shop_mappings WHERE chat_id = ?", (chat_id,)).fetchone()
        return res[0] if res else None
    finally:
        conn.close()

def set_shop_mapping(chat_id, website_os_name):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO shop_mappings (chat_id, website_os_name, updated_at) VALUES (?, ?, ?)",
            (chat_id, website_os_name, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()

def sync_website_shops(shop_list):
    """ Website မှ ဆိုင်နာမည်များကို Database ထဲသို့ အကုန်သွင်းပေးခြင်း (Cleaning included) """
    conn = get_connection()
    try:
        # အဟောင်းများကို အရင်ဖျက်မည်
        conn.execute("DELETE FROM website_shops")
        now = int(time.time())
        
        # (***) များကို ဖယ်ရှားပြီး Clean လုပ်မည်
        cleaned_shops = set()
        for name in shop_list:
            cleaned = clean_shop_name(name)
            if cleaned and cleaned != "Unknown Shop":
                cleaned_shops.add(cleaned)
        
        data = [(name, now) for name in cleaned_shops]
        conn.executemany("INSERT OR IGNORE INTO website_shops (name, created_at) VALUES (?, ?)", data)
        conn.commit()
        return len(data)
    finally:
        conn.close()

def get_all_website_shops():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT name FROM website_shops ORDER BY name ASC").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()

def is_website_shop_exists(name):
    """ Website shops ထဲတွင် အမည်အတိအကျ ရှိမရှိ စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        res = conn.execute("SELECT 1 FROM website_shops WHERE name = ?", (name,)).fetchone()
        return res is not None
    finally:
        conn.close()

def is_website_shop_exists(shop_name):
    """ Website shops ထဲတွင် အမည်အတိအကျ ရှိမရှိ စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        res = conn.execute("SELECT 1 FROM website_shops WHERE name = ?", (shop_name,)).fetchone()
        return res is not None
    finally:
        conn.close()

def get_unmapped_os_groups():
    """ Mapping မရှိသေးသော OS Group များကို ရှာပေးခြင်း """
    conn = get_connection()
    try:
        # os_groups ထဲမှာရှိပြီး shop_mappings ထဲမှာ မရှိသေးတာတွေကို ယူမည်
        # website_shops ထဲမှာ အတိအကျတူတာ ရှိနေရင်လည်း ကျော်မည်
        query = """
            SELECT DISTINCT g.chat_id, g.shop_name
            FROM os_groups g
            LEFT JOIN shop_mappings m ON g.chat_id = m.chat_id
            LEFT JOIN website_shops w ON g.shop_name = w.name
            WHERE m.chat_id IS NULL AND w.name IS NULL AND g.shop_name IS NOT NULL
        """
        return conn.execute(query).fetchall()
    finally:
        conn.close()

def get_website_suggestions(keyword, limit=5):
    """ Website Shops ထဲမှ အနီးစပ်ဆုံးတူသော နာမည်များကို ရှာပေးခြင်း """
    conn = get_connection()
    try:
        # Simple LIKE search for now
        query = "SELECT name FROM website_shops WHERE name LIKE ? LIMIT ?"
        res = conn.execute(query, (f'%{keyword}%', limit)).fetchall()
        return [r[0] for r in res]
    finally:
        conn.close()

def update_pickup_field(queue_id, field, value):
    """ pickup_queue ထဲရှိ field တစ်ခုကို update လုပ်ခြင်း """
    conn = get_connection()
    try:
        # SQL Injection ကာကွယ်ရန် field name ကို whitelist စစ်မည်
        allowed_fields = ['target_date', 'vehicle', 'remark', 'status']
        if field not in allowed_fields:
            log.error(f"❌ Invalid field name: {field}")
            return False
            
        conn.execute(f"UPDATE pickup_queue SET {field} = ? WHERE id = ?", (value, queue_id))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"❌ update_pickup_field Error: {e}")
        return False
    finally:
        conn.close()

def add_pickup_intermediate_msg(chat_id, orig_msg_id, intermediate_msg_id):
    """ Bot မှ ပို့လိုက်သော ကြားဖြတ်စာ ID ကို သိမ်းဆည်းခြင်း """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pickup_intermediate_messages (orig_msg_id, chat_id, intermediate_msg_id, created_at) VALUES (?, ?, ?, ?)",
            (orig_msg_id, chat_id, intermediate_msg_id, int(time.time()))
        )
        conn.commit()
    except Exception as e:
        log.error(f"❌ add_pickup_intermediate_msg Error: {e}")
    finally:
        conn.close()

def delete_pickup_intermediate_msgs(chat_id, orig_msg_id):
    """ Cleanup လုပ်ပြီးနောက် DB မှ record များကို ဖျက်ရန် """
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM pickup_intermediate_messages WHERE chat_id = ? AND orig_msg_id = ?",
            (chat_id, orig_msg_id)
        )
        conn.commit()
    except Exception as e:
        log.error(f"❌ delete_pickup_intermediate_msgs Error: {e}")
    finally:
        conn.close()

def get_pickup_intermediate_msgs(chat_id, orig_msg_id):
    """ သိမ်းထားသော ကြားဖြတ်စာ ID များကို ပြန်ယူခြင်း """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT intermediate_msg_id FROM pickup_intermediate_messages WHERE chat_id = ? AND orig_msg_id = ?",
            (chat_id, orig_msg_id)
        ).fetchall()
        return [r[0] for r in res]
    except Exception as e:
        log.error(f"❌ get_pickup_intermediate_msgs Error: {e}")
        return []
    finally:
        conn.close()

def save_temp_data(key, value):
    """ ယာယီ data သိမ်းဆည်းရန် (Callback length limit ကျော်လွှားရန်) """
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO temp_data (id, value, created_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
        conn.commit()
    finally:
        conn.close()

def get_temp_data(key):
    """ ယာယီ data ပြန်ယူရန် """
    conn = get_connection()
    try:
        res = conn.execute("SELECT value FROM temp_data WHERE id = ?", (key,)).fetchone()
        return res[0] if res else None
    finally:
        conn.close()

def get_pickup_order(queue_id):
    """ queue_id ဖြင့် pickup order အချက်အလက်များကို ယူခြင်း """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at FROM pickup_queue WHERE id = ?",
            (queue_id,)
        ).fetchone()
        return res
    finally:
        conn.close()

def get_waiting_confirm_order(chat_id):
    """ လက်ရှိ chat တွင် အတည်ပြုချက်စောင့်ဆိုင်းနေသော (WAITING_CONFIRM) အော်ဒါရှိမရှိ စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at "
            "FROM pickup_queue WHERE chat_id = ? AND status = 'WAITING_CONFIRM' "
            "ORDER BY created_at DESC LIMIT 1",
            (chat_id,)
        ).fetchone()
        return res
    finally:
        conn.close()

# --- [ Version 4.0 Global & Group Settings Helpers ] ---
def get_group_ai_status(chat_id):
    """ Group တစ်ခုချင်းစီ၏ AI Status ကို ရယူခြင်း (Default: ON) """
    conn = get_connection()
    try:
        res = conn.execute("SELECT ai_status FROM group_settings WHERE chat_id = ?", (chat_id,)).fetchone()
        return res[0] if res else 'ON'
    finally:
        conn.close()

def set_group_ai_status(chat_id, status):
    """ Group တစ်ခုချင်းစီ၏ AI Status ကို Update လုပ်ခြင်း ('ON' or 'OFF') """
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO group_settings (chat_id, ai_status) VALUES (?, ?)", (chat_id, status))
        conn.commit()
    finally:
        conn.close()

def get_ai_global_status():
    """ AI Global Status ကို settings table မှ ရယူခြင်း (Default: ON) """
    return get_setting('global_ai_answer', 'ON')

def set_ai_global_status(status):
    """ AI Global Status ကို settings table တွင် update လုပ်ခြင်း ('ON' or 'OFF') """
    set_setting('global_ai_answer', status)

def get_auto_pickup_global_status():
    """ Auto Pickup Global Status ကို settings table မှ ရယူခြင်း (Default: ON) """
    return get_setting('global_auto_pickup', 'ON')

def set_auto_pickup_global_status(status):
    """ Auto Pickup Global Status ကို settings table တွင် update လုပ်ခြင်း ('ON' or 'OFF') """
    set_setting('global_auto_pickup', status)

def get_alert_system_global_status():
    """ Alert System Global Status ကို settings table မှ ရယူခြင်း (Default: ON) """
    return get_setting('global_alert_system', 'ON')

def set_alert_system_global_status(status):
    """ Alert System Global Status ကို settings table တွင် update လုပ်ခြင်း ('ON' or 'OFF') """
    set_setting('global_alert_system', status)

# --- [ User State Helpers ] ---
def get_user_state(user_id):
    """ User ၏ out_of_scope_count နှင့် human_intervention_needed status ကို ယူခြင်း """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT out_of_scope_count, human_intervention_needed FROM user_states WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return res if res else (0, 0)
    finally:
        conn.close()

def increment_out_of_scope(user_id):
    """ User ၏ out_of_scope_count ကို ၁ တိုးခြင်း """
    conn = get_connection()
    try:
        # အရင်ရှိမရှိ စစ်ပြီး INSERT သို့မဟုတ် UPDATE လုပ်မည်
        res = conn.execute("SELECT out_of_scope_count FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
        now = int(time.time())
        if res:
            new_count = res[0] + 1
            conn.execute(
                "UPDATE user_states SET out_of_scope_count = ?, last_updated = ? WHERE user_id = ?",
                (new_count, now, user_id)
            )
        else:
            new_count = 1
            conn.execute(
                "INSERT INTO user_states (user_id, out_of_scope_count, last_updated) VALUES (?, ?, ?)",
                (user_id, 1, now)
            )
        conn.commit()
        return new_count
    finally:
        conn.close()

def set_human_intervention(user_id, needed=1):
    """ User ကို human_intervention_needed အဖြစ် သတ်မှတ်ခြင်း (Mute AI) """
    conn = get_connection()
    try:
        now = int(time.time())
        res = conn.execute("SELECT 1 FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
        if res:
            conn.execute(
                "UPDATE user_states SET human_intervention_needed = ?, last_updated = ? WHERE user_id = ?",
                (needed, now, user_id)
            )
        else:
            conn.execute(
                "INSERT INTO user_states (user_id, human_intervention_needed, last_updated) VALUES (?, ?, ?)",
                (user_id, needed, now)
            )
        conn.commit()
    finally:
        conn.close()

def reset_user_state(user_id):
    """ User ၏ state ကို reset လုပ်ခြင်း (Admin မှ ပြန်ဖွင့်ပေးသည့်အခါ သုံးရန်) """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE user_states SET out_of_scope_count = 0, human_intervention_needed = 0, last_updated = ? WHERE user_id = ?",
            (int(time.time()), user_id)
        )
        conn.commit()
    finally:
        conn.close()

