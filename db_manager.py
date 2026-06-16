# Version: 4.1 (Shop mapping lookup + AI feedback/context)
import sqlite3
import time
import os
import re
import pytz
from datetime import datetime, timedelta
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
    🤝CarryMan နှင့် Emoji များကို ဖယ်ရှားမည်။
    """
    if not raw_name:
        return "Unknown Shop"
    
    import re
    # Unicode string အဖြစ် သေချာအောင်လုပ်ခြင်း
    raw_str = str(raw_name)
    
    # 1. 🤝 suffix နှင့် Carry Man / CarryMan branding ကို ဖယ်ရှားခြင်း
    if '🤝' in raw_str:
        raw_str = raw_str.split('🤝')[0]
    cleaned = re.sub(r'\s*Carry\s*Man\s*', '', raw_str, flags=re.IGNORECASE)
    
    # 2. (***) ဖြင့် ရေးထားသည်များကို ဖယ်ရှားခြင်း
    cleaned = re.sub(r'\(.*?\)', '', cleaned)
    
    # 3. Noise characters များ (ï½ စသည်ဖြင့်) နှင့် Emoji များကို ဖယ်ရှားခြင်း
    # Burmese Unicode Range: \u1000-\u109F
    # Burmese Extended-A: \uAA60-\uAA7F
    # Burmese Extended-B: \uA9E0-\uA9FF
    # We keep: Burmese characters, English letters (A-Z, a-z), Numbers (0-9), and Spaces
    # \w ကို မသုံးဘဲ တိကျသော range များသာ သုံးမည် (Noise characters များ \w ထဲ ပါသွားနိုင်၍)
    cleaned = re.sub(r'[^a-zA-Z0-9\s\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF]', '', cleaned)
    
    # 4. ပိုနေသော space များကို ရှင်းလင်းခြင်း
    cleaned = " ".join(cleaned.split()).strip()
    
    # အကယ်၍ clean လုပ်ပြီးနောက် ဘာမှမကျန်တော့ပါက မူရင်းနာမည်မှ emoji မပါသည်ကို ပြန်ယူရန် ကြိုးစားမည်
    if not cleaned:
        # Emoji မပါသော character များသာ ယူမည်
        fallback = "".join(c for c in raw_str if ord(c) < 0x1F600)
        cleaned = " ".join(fallback.split()).strip()

    return cleaned or raw_str or "Unknown Shop"

def get_connection():
    """ Database ချိတ်ဆက်မှုအား Multithreading နှင့် မြန်နှုန်းမြင့် WAL Mode သတ်မှတ်ခြင်း (Timeout 60s ထည့်ထားသည်) """
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=60)
        # Unicode handling အတွက် text_factory ကို str သတ်မှတ်ခြင်း (Python 3 တွင် default ဖြစ်သော်လည်း သေချာစေရန်)
        conn.text_factory = str
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA busy_timeout=30000;') # 30s wait on write contention (multi-process safety)
        conn.execute('PRAGMA cache_size=-64000;') # 64MB Cache
        conn.execute('PRAGMA temp_store=MEMORY;')
        conn.execute('PRAGMA mmap_size=268435456;') # 256MB Mmap
        return conn
    except sqlite3.Error as e:
        log.error(f"❌ Database Connection Error: {e}")
        raise

@contextmanager
def connection_scope(max_retries=5):
    """ Database connection ကို context manager အဖြစ် အသုံးပြုရန် (Auto-commit/rollback + Retry ပါဝင်သည်) """
    last_exception = None
    for attempt in range(max_retries):
        conn = get_connection()
        try:
            yield conn
            conn.commit()
            return  # Success — exit retry loop
        except sqlite3.OperationalError as e:
            conn.rollback()
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                wait = 0.1 * (2 ** attempt)  # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s
                log.warning(f"⚠️ Database locked. Retrying in {wait:.1f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                last_exception = e
                continue
            raise e
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    # All retries exhausted
    log.error(f"❌ Database lock persists after {max_retries} retries.")
    raise last_exception

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
                     created_at INTEGER,
                     shop_msg_id INTEGER
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

        # Facebook Messenger Integration Tables
        c.execute('''CREATE TABLE IF NOT EXISTS fb_tasks (
                     fb_user_id TEXT PRIMARY KEY,
                     fb_user_name TEXT,
                     tg_group_msg_id INTEGER,
                     staff_id INTEGER,
                     staff_name TEXT,
                     status TEXT DEFAULT 'PENDING',
                     last_text TEXT,
                     updated_at INTEGER
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS fb_chat_sessions (
                     staff_id INTEGER PRIMARY KEY,
                     fb_user_id TEXT,
                     started_at INTEGER
                   )''')
        
        # ၂။ Migration Engine (Column အသစ်များ အလိုအလျောက် စစ်ဆေးထည့်သွင်းခြင်း)
        migrations = {
            "staff": ["branch TEXT", "dept TEXT"],
            "message_logs": ["text TEXT", "resolved_by TEXT", "resolve_time INTEGER", "topic_id INTEGER", "media_id TEXT", "is_manual INTEGER DEFAULT 0", "category TEXT", "intent TEXT", "summary TEXT"],
            "os_groups": ["last_read_message_id INTEGER DEFAULT 0", "target_chat_id INTEGER", "target_topic_id INTEGER"],
            "alert_tracking": ["created_at INTEGER", "esc_msg_id INTEGER", "esc_tier2_msg_id INTEGER", "linked_msg_ids TEXT DEFAULT '[]'", "linked_customer_ids TEXT DEFAULT '[]'", "updates_text TEXT DEFAULT ''"],
            "feedback_logs": ["chat_id INTEGER", "topic_id INTEGER", "category TEXT", "original_text TEXT", "staff_id INTEGER"],
            "master_rules": ["chat_id INTEGER", "topic_id INTEGER", "rule_content TEXT"],
            "knowledge_base": ["category TEXT", "question TEXT", "answer TEXT", "tags TEXT", "level INTEGER DEFAULT 1", "last_updated INTEGER"],
            "pickup_queue": ["shop_msg_id INTEGER"],
            "fb_tasks": ["fb_user_name TEXT"],
            "user_states": ["last_ai_query TEXT", "last_location_id TEXT"],
            "ai_feedback_pending": ["source_ref TEXT"],
            "ai_feedback_logs": ["source_ref TEXT"],
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
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ai_auto_delivery_reply', 'OFF')")

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

        # ၅.၁ AI conversation context (per user + chat, ~1 hour / 3 turns)
        c.execute('''CREATE TABLE IF NOT EXISTS ai_chat_context (
                     user_id INTEGER NOT NULL,
                     chat_id INTEGER NOT NULL,
                     turns_json TEXT NOT NULL DEFAULT '[]',
                     updated_at INTEGER,
                     PRIMARY KEY (user_id, chat_id)
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS ai_feedback_pending (
                     token TEXT PRIMARY KEY,
                     user_id INTEGER NOT NULL,
                     chat_id INTEGER NOT NULL,
                     query TEXT,
                     reply TEXT,
                     topic TEXT,
                     location_id TEXT,
                     source_ref TEXT,
                     created_at INTEGER,
                     completed INTEGER DEFAULT 0
                   )''')

        c.execute('''CREATE TABLE IF NOT EXISTS ai_feedback_logs (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     chat_id INTEGER,
                     query TEXT,
                     reply TEXT,
                     topic TEXT,
                     location_id TEXT,
                     source_ref TEXT,
                     rating TEXT,
                     reason TEXT,
                     created_at INTEGER
                   )''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_feedback_logs_time ON ai_feedback_logs(created_at)")

        # ၆။ Performance Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_status_time ON message_logs(status, timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_chat_topic_status ON message_logs(chat_id, topic_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_os_groups_chat_topic ON os_groups(chat_id, topic_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_feedback_logs_chat_topic ON feedback_logs(chat_id, topic_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_master_rules_chat_topic ON master_rules(chat_id, topic_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_chat_time ON message_logs(chat_id, timestamp)")

        conn.commit()
        
        # ၇။ Database Maintenance (PRAGMA optimize)
        c.execute("PRAGMA optimize;")
        
        log.info("✅ Database (Version 5.0) Initialized Successfully.")
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            log.warning("⚠️ Database is locked. Skipping migration.")
        else:
            log.error(f"❌ Database Init Error: {e}")
    finally:
        conn.close()
        # Perform initial maintenance
        try:
            db_maintenance()
        except Exception:
            pass

def db_maintenance():
    """
    Database ကျန်းမာရေးအတွက် ပုံမှန်လုပ်ဆောင်ပေးရမည့် Maintenance လုပ်ငန်းစဉ်များ။
    WAL file ကို ရှင်းထုတ်ခြင်း၊ Index များကို Optimize လုပ်ခြင်းနှင့် နေရာလွတ်များ ပြန်ယူခြင်းတို့ ပါဝင်သည်။
    """
    conn = get_connection()
    try:
        log.info("🧹 Starting Database Maintenance...")
        # ၁။ WAL Checkpoint (ယာယီဖိုင်မှ data များကို main db ထဲသို့ အကုန်သွင်းပြီး WAL ဖိုင်ကို 0 size လုပ်ခြင်း)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        
        # ၂။ Optimize (Query performance ကောင်းမွန်စေရန် index များကို ပြန်စီခြင်း)
        conn.execute("PRAGMA optimize;")
        
        # ၃။ Vacuum (ဖျက်လိုက်သော data များနေရာတွင် ကျန်ခဲ့သော နေရာလွတ်များကို ပြန်သိမ်းပြီး file size လျှော့ခြင်း)
        # မှတ်ချက် - Vacuum သည် DB တစ်ခုလုံးကို lock ချတတ်သဖြင့် လိုအပ်မှသာ သုံးသင့်သည်။
        # conn.execute("VACUUM;")
        
        log.info("✅ Database Maintenance Completed.")
    except sqlite3.Error as e:
        log.error(f"❌ Database Maintenance Error: {e}")
    finally:
        conn.close()

def update_last_read_id(chat_id, topic_id, last_id):
    """ Smart Polling အတွက် နောက်ဆုံးဖတ်ပြီးသား Message ID ကို မှတ်သားရန် """
    conn = get_connection()
    try:
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(chat_id).replace("-100", ""))
        conn.execute(
            "UPDATE os_groups SET last_read_message_id = ? WHERE chat_id IN (?, ?) AND topic_id = ?",
            (last_id, chat_id, clean_id, topic_id)
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

def upsert_staff_batch(rows):
    """rows: list of (user_id, name, branch, dept)"""
    if not rows:
        return False, 0
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO staff VALUES (?, ?, ?, ?)",
            [(int(uid), name, branch or "General", dept or "General") for uid, name, branch, dept in rows],
        )
        conn.commit()
        return True, len(rows)
    except Exception as e:
        log.error(f"❌ upsert_staff_batch Error: {e}")
        return False, 0
    finally:
        conn.close()

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

# --- [ Facebook Messenger Helpers ] ---
def get_fb_task(fb_user_id):
    conn = get_connection()
    res = conn.execute("SELECT * FROM fb_tasks WHERE fb_user_id = ?", (fb_user_id,)).fetchone()
    conn.close()
    if res:
        return {
            'fb_user_id': res[0],
            'fb_user_name': res[1],
            'tg_group_msg_id': res[2],
            'staff_id': res[3],
            'staff_name': res[4],
            'status': res[5],
            'last_text': res[6],
            'updated_at': res[7]
        }
    return None

def upsert_fb_task(fb_user_id, fb_user_name, tg_group_msg_id, status, last_text):
    conn = get_connection()
    now = int(time.time())
    conn.execute("""
        INSERT INTO fb_tasks (fb_user_id, fb_user_name, tg_group_msg_id, status, last_text, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(fb_user_id) DO UPDATE SET
            fb_user_name = excluded.fb_user_name,
            tg_group_msg_id = excluded.tg_group_msg_id,
            status = excluded.status,
            last_text = excluded.last_text,
            updated_at = excluded.updated_at
    """, (fb_user_id, fb_user_name, tg_group_msg_id, status, last_text, now))
    conn.commit()
    conn.close()

def update_fb_task_status(fb_user_id, status, staff_id=None, staff_name=None):
    conn = get_connection()
    now = int(time.time())
    if staff_id:
        conn.execute("UPDATE fb_tasks SET status = ?, staff_id = ?, staff_name = ?, updated_at = ? WHERE fb_user_id = ?",
                     (status, staff_id, staff_name, now, fb_user_id))
    else:
        conn.execute("UPDATE fb_tasks SET status = ?, updated_at = ? WHERE fb_user_id = ?",
                     (status, now, fb_user_id))
    conn.commit()
    conn.close()

def delete_fb_task(fb_user_id):
    conn = get_connection()
    conn.execute("DELETE FROM fb_tasks WHERE fb_user_id = ?", (fb_user_id,))
    conn.commit()
    conn.close()

def get_active_fb_session(staff_id):
    conn = get_connection()
    res = conn.execute("SELECT fb_user_id FROM fb_chat_sessions WHERE staff_id = ?", (staff_id,)).fetchone()
    conn.close()
    return res[0] if res else None

def start_fb_session(staff_id, fb_user_id):
    conn = get_connection()
    now = int(time.time())
    conn.execute("INSERT OR REPLACE INTO fb_chat_sessions (staff_id, fb_user_id, started_at) VALUES (?, ?, ?)",
                 (staff_id, fb_user_id, now))
    conn.commit()
    conn.close()

def end_fb_session(staff_id):
    conn = get_connection()
    conn.execute("DELETE FROM fb_chat_sessions WHERE staff_id = ?", (staff_id,))
    conn.commit()
    conn.close()

def get_staff_by_fb_user(fb_user_id):
    conn = get_connection()
    res = conn.execute("SELECT staff_id FROM fb_chat_sessions WHERE fb_user_id = ?", (fb_user_id,)).fetchone()
    conn.close()
    return res[0] if res else None

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
    """ Message သည် Manual Alert (/a) ဖြစ်မဖြစ် စစ်ဆေးခြင်း (linked parent ပါစစ်) """
    conn = get_connection()
    try:
        check_id = get_parent_msg_id(msg_id, chat_id)
        res = conn.execute("SELECT is_manual FROM message_logs WHERE msg_id = ? AND chat_id = ?", (check_id, chat_id)).fetchone()
        return (res[0] == 1) if res else False
    finally:
        conn.close()

def set_manual_alert(msg_id, chat_id, topic_id=None, user_id=0, text=None, timestamp=None, media_id=None):
    """ Message ကို Manual Alert အဖြစ် သတ်မှတ်ခြင်း """
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE message_logs SET is_manual = 1 WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id))
        if cur.rowcount == 0 and text is not None:
            safe_topic_id = topic_id if topic_id and topic_id != 0 else 1
            ts = timestamp if timestamp is not None else int(time.time())
            conn.execute(
                "INSERT OR IGNORE INTO message_logs (msg_id, chat_id, topic_id, user_id, text, timestamp, status, media_id, is_manual) VALUES (?, ?, ?, ?, ?, ?, 'ALERTED', ?, 1)",
                (msg_id, chat_id, safe_topic_id, user_id, text, ts, media_id)
            )
            conn.execute("UPDATE message_logs SET is_manual = 1 WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id))
        conn.commit()
    finally:
        conn.close()

def has_active_pickup_flow(msg_id, chat_id):
    """Auto-pickup queue ထဲတွင် မပြီးသေးသော order ရှိပါက react/reply resolve မလုပ်ပါ"""
    conn = get_connection()
    try:
        res = conn.execute(
            """SELECT 1 FROM pickup_queue
               WHERE orig_msg_id = ? AND chat_id = ?
               AND status IN ('PENDING', 'PROCESSING', 'WAITING_CONFIRM', 'WAITING_SETUP')
               LIMIT 1""",
            (msg_id, chat_id)
        ).fetchone()
        return bool(res)
    finally:
        conn.close()

def message_was_staff_handled(msg_id, chat_id):
    """Staff reply/reaction သို့မဟုတ် SLA alert ပို့ပြီးသော message"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT resolved_by, status FROM message_logs WHERE msg_id = ? AND chat_id = ?",
            (msg_id, chat_id)
        ).fetchone()
        if not row:
            return False
        resolved_by, status = row
        if resolved_by:
            return True
        if status in ('ALERTED', 'ESCALATED'):
            return True
        tracking = conn.execute(
            "SELECT 1 FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ? LIMIT 1",
            (msg_id, chat_id)
        ).fetchone()
        return bool(tracking)
    finally:
        conn.close()

def cancel_pickup_queue_for_message(orig_msg_id, chat_id):
    """Pickup flow queue entry ကို message တစ်ခုအတွက် ရှင်းခြင်း"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?",
            (orig_msg_id, chat_id)
        )
        conn.commit()
    finally:
        conn.close()

def resolve_message(msg_id, chat_id, staff_name, method='Reply', topic_id=None, status='RESOLVED'):
    """
    Message တစ်ခု သို့မဟုတ် Topic တစ်ခုလုံးကို Resolved အဖြစ် သတ်မှတ်ခြင်း။
    topic_id ပါလာလျှင် ထို topic ထဲက pending များကိုသာ resolve လုပ်မည်။
    """
    if msg_id != 0 and method != 'Done Button' and is_manual_alert(msg_id, chat_id):
        log.info(f"🛡️ Manual alert {msg_id} in {chat_id} — resolve blocked (method={method}). Done button only.")
        return

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
        query = "UPDATE message_logs SET status='RESOLVED', resolved_by='System (Auto-Expired)', resolve_time=? WHERE status IN ('ALERTED', 'ESCALATED') AND timestamp < ? AND (is_manual IS NULL OR is_manual = 0)"
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

def reset_message_timestamp(msg_id, chat_id, new_timestamp):
    """ Pickup Cancel လုပ်သည့်အခါ ၁၅ မိနစ် alert system က အခုမှစပြီး ပြန်ရေတွက်စေရန် timestamp ကို reset လုပ်ခြင်း """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE message_logs SET timestamp = ? WHERE msg_id = ? AND chat_id = ?",
            (new_timestamp, msg_id, chat_id)
        )
        conn.commit()
    except Exception as e:
        log.error(f"❌ reset_message_timestamp failed for msg_id={msg_id}, chat_id={chat_id}: {e}")
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
    
    # 💡 Chat ID Mismatch Fix (Telethon vs Telebot)
    # Telebot uses -100... while Telethon might store without it.
    # We handle both cases: removing -100 and adding -100
    s_id = str(chat_id)
    clean_id = int(s_id.replace("-100", ""))
    alt_id = int(f"-100{clean_id}") if not s_id.startswith("-100") else chat_id
    
    c.execute("SELECT shop_name FROM os_groups WHERE chat_id IN (?, ?, ?) LIMIT 1", (chat_id, clean_id, alt_id))
    g_res = c.fetchone()
    if g_res:
        shop_name = clean_shop_name(g_res[0])
    else:
        # 💡 Fallback: shop_mappings table (GSheet has the name but os_groups missing routing)
        c.execute("SELECT website_os_name FROM shop_mappings WHERE chat_id IN (?, ?) LIMIT 1", (chat_id, clean_id))
        map_res = c.fetchone()
        shop_name = clean_shop_name(map_res[0]) if map_res else "Unknown Shop"
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
    try:
        # 💡 Chat ID Mismatch Fix: Handle both -100 prefix and without it
        clean_id = int(str(chat_id).replace("-100", ""))
        alt_id = int(f"-100{clean_id}") if not str(chat_id).startswith("-100") else chat_id
        
        res = conn.execute(
            "SELECT alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids, esc_tier2_msg_id, updates_text "
            "FROM alert_tracking WHERE original_msg_id = ? AND chat_id IN (?, ?, ?)",
            (original_msg_id, chat_id, clean_id, alt_id)
        ).fetchone()
        return res
    finally:
        conn.close()

def update_alert_updates_text(original_msg_id, chat_id, updates_text):
    """ Alert Tracking တွင် updates_text ကို update လုပ်ခြင်း """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE alert_tracking SET updates_text = ? WHERE original_msg_id = ? AND chat_id = ?",
            (updates_text, original_msg_id, chat_id)
        )
        conn.commit()
    finally:
        conn.close()

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
    
    💡 topic_id=0/None → topic_id=1 (General Topic) အဖြစ် auto-convert.
    """
    try:
        # ၁။ Type Casting (String ဖြစ်နေပါက Integer သို့ ပြောင်းခြင်း)
        c_id = int(chat_id)
        t_id = int(topic_id) if topic_id is not None else 0
        
        # 💡 topic_id=0 → General Topic (1) အဖြစ် convert
        if t_id == 0:
            t_id = 1
        
        conn = get_connection()
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(c_id).replace("-100", ""))
        res = conn.execute(
            "SELECT target_chat_id, target_topic_id FROM os_groups WHERE chat_id IN (?, ?) AND topic_id = ? LIMIT 1",
            (c_id, clean_id, t_id)
        ).fetchone()
        conn.close()
        
        if res:
            return res
        
        # 💡 Fallback: topic_id အတိအကျမတွေ့ရင် chat_id တူတဲ့ ဘယ် route ကိုမဆို ယူမည်
        conn = get_connection()
        res = conn.execute(
            "SELECT target_chat_id, target_topic_id FROM os_groups WHERE chat_id IN (?, ?) AND target_chat_id IS NOT NULL AND target_topic_id IS NOT NULL LIMIT 1",
            (c_id, clean_id)
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
    💡 topic_id=0 → topic_id=1 (General Topic) အဖြစ် auto-convert.
    """
    # 💡 topic_id=0/None → General Topic (1)
    if topic_id == 0 or topic_id is None:
        topic_id = 1
    
    conn = get_connection()
    try:
        # ၁။ အရင်ရှိမရှိ စစ်ဆေးခြင်း (သို့မဟုတ် INSERT OR REPLACE သုံးခြင်း)
        # os_groups မှာ chat_id, topic_id က primary key သို့မဟုတ် unique ဖြစ်ရမည်
        # လက်ရှိ schema အရ UPDATE အရင်လုပ်ကြည့်ပြီး rowcount 0 ဖြစ်လျှင် INSERT လုပ်မည်
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(chat_id).replace("-100", ""))
        cursor = conn.execute(
            "UPDATE os_groups SET target_chat_id = ?, target_topic_id = ? WHERE chat_id IN (?, ?) AND topic_id = ?",
            (target_chat_id, target_topic_id, chat_id, clean_id, topic_id)
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
        
        # 💡 Auto-Cleanup: Delete stale entries with same chat_id & target_topic_id but different topic_id
        # (e.g., GSheet had wrong topic_id=89, staff corrected to topic_id=4 for same target_topic_id=37)
        deleted = conn.execute(
            "DELETE FROM os_groups WHERE chat_id IN (?, ?) AND target_topic_id = ? AND topic_id != ?",
            (chat_id, clean_id, target_topic_id, topic_id)
        )
        if deleted.rowcount > 0:
            log.info(f"🧹 Auto-cleaned {deleted.rowcount} stale routing(s) for chat {chat_id} target_topic {target_topic_id} (correct topic_id={topic_id})")
            
        conn.commit()
    except Exception as e:
        log.error(f"❌ Failed to update routing entry: {e}")
    finally:
        conn.close()

# --- [ OS Group & Analytics ] ---
def check_if_os_group(chat_id):
    conn = get_connection()
    # 💡 Chat ID Mismatch Fix
    clean_id = int(str(chat_id).replace("-100", ""))
    res = conn.execute("SELECT 1 FROM os_groups WHERE chat_id IN (?, ?) LIMIT 1", (chat_id, clean_id)).fetchone()
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

def get_distinct_os_group_chats():
    """Distinct OS Telegram group chat_ids for staff membership sync."""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT DISTINCT chat_id, shop_name FROM os_groups WHERE chat_id IS NOT NULL ORDER BY shop_name"
        ).fetchall()
    finally:
        conn.close()

def normalize_bot_chat_id(chat_id):
    """Telethon/Bot API chat_id format ကို Telegram Bot API supergroup ID (-100...) သို့ ပြောင်းသည်."""
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return chat_id
    s = str(cid)
    if s.startswith("-100"):
        return cid
    if cid < 0:
        return cid
    return int(f"-100{cid}")

def dedupe_shop_mapping_chat_ids():
    """Short-format chat_id များကို -100... format သို့ merge/cleanup လုပ်ခြင်း"""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT chat_id FROM shop_mappings").fetchall()
        deleted = 0
        migrated = 0
        for (cid,) in rows:
            full_id = normalize_bot_chat_id(cid)
            if full_id == cid:
                continue
            existing = conn.execute("SELECT 1 FROM shop_mappings WHERE chat_id = ?", (full_id,)).fetchone()
            if existing:
                conn.execute("DELETE FROM shop_mappings WHERE chat_id = ?", (cid,))
                deleted += 1
            else:
                conn.execute("UPDATE shop_mappings SET chat_id = ? WHERE chat_id = ?", (full_id, cid))
                migrated += 1

        for (cid,) in conn.execute("SELECT DISTINCT chat_id FROM os_groups").fetchall():
            full_id = normalize_bot_chat_id(cid)
            if full_id == cid:
                continue
            if conn.execute("SELECT 1 FROM os_groups WHERE chat_id = ? LIMIT 1", (full_id,)).fetchone():
                conn.execute("DELETE FROM os_groups WHERE chat_id = ?", (cid,))
            else:
                conn.execute(
                    "UPDATE os_groups SET chat_id = ?, group_id = ? WHERE chat_id = ?",
                    (full_id, full_id, cid)
                )

        conn.commit()
        if deleted or migrated:
            log.info(f"🧹 dedupe_shop_mapping_chat_ids: deleted={deleted}, migrated={migrated}")
        return deleted + migrated
    except Exception as e:
        conn.rollback()
        log.error(f"❌ dedupe_shop_mapping_chat_ids Error: {e}")
        return 0
    finally:
        conn.close()

def get_os_group_topic_count(chat_id):
    """OS group routing row အရေအတွက် (dedupe scoring အတွက်)"""
    conn = get_connection()
    try:
        norm = normalize_bot_chat_id(chat_id)
        row = conn.execute("SELECT COUNT(*) FROM os_groups WHERE chat_id = ?", (norm,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()

def dedupe_shop_mapping_by_website_name():
    """Website name တူညီသော mapping များထဲမှ os_groups ရှိ/နောက်ဆုံး row ကိုသာ ထားခြင်း"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT chat_id, website_os_name, updated_at FROM shop_mappings WHERE TRIM(COALESCE(website_os_name, '')) != ''"
        ).fetchall()
        winners = {}
        for cid, name, updated_at in rows:
            key = name.strip().lower()
            og_count = conn.execute("SELECT COUNT(*) FROM os_groups WHERE chat_id = ?", (cid,)).fetchone()[0]
            score = (og_count * 1_000_000) + (updated_at or 0)
            prev = winners.get(key)
            if not prev or score > prev[0]:
                winners[key] = (score, cid)

        deleted = 0
        for cid, name, _ in rows:
            key = name.strip().lower()
            winner_id = winners[key][1]
            if cid == winner_id:
                continue
            conn.execute("DELETE FROM shop_mappings WHERE chat_id = ?", (cid,))
            conn.execute("DELETE FROM os_groups WHERE chat_id = ?", (cid,))
            deleted += 1

        conn.commit()
        if deleted:
            log.info(f"🧹 dedupe_shop_mapping_by_website_name: deleted={deleted}")
        return deleted
    except Exception as e:
        conn.rollback()
        log.error(f"❌ dedupe_shop_mapping_by_website_name Error: {e}")
        return 0
    finally:
        conn.close()

def dedupe_shop_mappings():
    """Chat ID format + website name duplicate cleanup"""
    total = dedupe_shop_mapping_chat_ids()
    total += dedupe_shop_mapping_by_website_name()
    return total

def get_os_group_broadcast_targets():
    """OS Group တစ်ခုလျှင် တစ်ကြိမ်သာ — General (1) အရင်၊ ပြီးမှ DB topic fallback."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT chat_id, shop_name, topic_id, target_topic_id
            FROM os_groups
            WHERE chat_id IS NOT NULL
            ORDER BY shop_name, target_topic_id
            """
        ).fetchall()
    finally:
        conn.close()

    grouped = {}
    for chat_id, shop_name, topic_id, target_tid in rows:
        norm = normalize_bot_chat_id(chat_id)
        entry = grouped.setdefault(norm, {"shop_name": shop_name, "pickup_topic": None, "all_topics": []})
        if topic_id not in entry["all_topics"]:
            entry["all_topics"].append(topic_id)
        if target_tid == 1 and entry["pickup_topic"] is None:
            entry["pickup_topic"] = topic_id

    result = []
    for chat_id, data in sorted(grouped.items(), key=lambda item: item[1]["shop_name"] or ""):
        topic_ids = [1]
        if data["pickup_topic"] is not None and data["pickup_topic"] not in topic_ids:
            topic_ids.append(data["pickup_topic"])
        for topic_id in data["all_topics"]:
            if topic_id not in topic_ids:
                topic_ids.append(topic_id)
        if 2 not in topic_ids:
            topic_ids.append(2)
        result.append((chat_id, data["shop_name"], topic_ids))
    return result

def delete_os_group_by_chat_id(chat_id):
    conn = get_connection()
    try:
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(chat_id).replace("-100", ""))
        conn.execute("DELETE FROM os_groups WHERE chat_id IN (?, ?)", (chat_id, clean_id))
        conn.commit()
    finally:
        conn.close()

def get_pending_counts_by_shop():
    conn = get_connection()
    try:
        # 💡 Chat ID Mismatch Fix: Use a more flexible JOIN that handles both ID formats
        return conn.execute(
            """
            SELECT o.shop_name, COUNT(m.msg_id)
            FROM message_logs m
            JOIN os_groups o ON (m.chat_id = o.chat_id OR CAST(REPLACE(CAST(m.chat_id AS TEXT), '-100', '') AS INTEGER) = o.chat_id)
            WHERE m.status='PENDING' AND m.status != 'HANDLED_BY_AI'
            GROUP BY o.chat_id
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

def log_feedback(chat_id, msg_id, text, category='NOT_PICKUP', staff_id=0):
    """ AI Feedback ကို သိမ်းဆည်းခြင်း (Simplified version for cancellations) """
    conn = get_connection()
    try:
        # Get topic_id from message_logs if available
        res = conn.execute("SELECT topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
        topic_id = res[0] if res else 1
        
        conn.execute(
            "INSERT INTO feedback_logs (message_id, chat_id, topic_id, category, original_text, staff_id, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, chat_id, topic_id, category, text, staff_id, int(time.time()))
        )
        conn.commit()
        log.info(f"📝 Feedback logged for AI: {category} (Msg: {msg_id})")
    except Exception as e:
        log.error(f"❌ log_feedback Error: {e}")
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

def _extract_search_tokens(query):
    """Query ကို keyword/token အဖြစ် ခွဲထုတ်ခြင်း (Myanmar + English support)."""
    import re
    if not query:
        return []

    def _add_token(bucket, seen, token):
        token = str(token).strip()
        if not token or len(token) < 2:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        bucket.append(token)

    raw_tokens = re.findall(r'[\u1000-\u109F]+|[a-zA-Z0-9]{2,}', str(query))
    seen = set()
    tokens = []

    myanmar_split = re.compile(
        r'(?:ကို|မှာ|သို့|နဲ့|လို့|ရလား|ရမလား|ပို့|လာ|ရ|မလား|ဆို|လဲ|လား|နော်|နော|ခင်ဗျ|ခင်ဗျာ|ဟု|အား|တွင်|နှင့်)'
    )

    for token in raw_tokens:
        _add_token(tokens, seen, token)

        if re.search(r'[\u1000-\u109F]', token):
            for part in myanmar_split.split(token):
                _add_token(tokens, seen, part)

            for myanmar_name, aliases in _LOCATION_ALIASES.items():
                if myanmar_name in token or token.startswith(myanmar_name[:3]):
                    _add_token(tokens, seen, myanmar_name)
                    for alias in aliases:
                        _add_token(tokens, seen, alias)

        token_lower = token.lower()
        for myanmar_name, aliases in _LOCATION_ALIASES.items():
            if token_lower in [a.lower() for a in aliases]:
                _add_token(tokens, seen, myanmar_name)
                for alias in aliases:
                    _add_token(tokens, seen, alias)

    return tokens[:16]


_LOCATION_ALIASES = {
    "မြစ်ကြီးနား": ["Myitkyina", "myitkyina"],
    "ကျိုင်းတုံ": ["Kengtung", "kengtung"],
    "မုံရွာ": ["Monywa", "monywa"],
    "သထုံ": ["Dawei", "dawei"],
    "မန္တလေး": ["Mandalay", "mandalay"],
    "နေပြည်တော်": ["Naypyidaw", "naypyidaw"],
    "ပြင်ဦးလွင်": ["Pyin Oo Lwin", "pyin oo lwin"],
    "လားရှိုး": ["Lashio", "lashio"],
    "တာချို": ["Taunggyi", "taunggyi"],
    "စစ်တွေ": ["Sittwe", "sittwe"],
    "ဘားအံ": ["Hpa-An", "hpa-an", "Hpa An"],
    "ရန်ကုန်": ["Yangon", "yangon", "Rangoon"],
    "ကျိုက်ထို": ["Kyaikto", "kyaikto"],
    "ပုသိမ်": ["Pathein", "pathein"],
    "မကွေး": ["Magway", "magway"],
    "မော်လမြိုင်": ["Mawlamyine", "mawlamyine"],
}

_ISLAND_MARKERS = ("ကျွန်း", "gyun", "island", "mawlamyinegyun")


def _extract_location_exclusions(query):
    """Query ထဲမှ မလိုချင်တဲ့ နေရာသင်္ကေတများ ထုတ်ယူခြင်း。"""
    import re
    q = str(query or "")
    q_lower = q.lower()
    exclusions = set()

    if re.search(r"မော်လမြိုင်ကျွ\s*န်း?\s*မဟုတ်", q) or re.search(r"ကျွ\s*န်း?\s*မဟုတ်", q):
        exclusions.update(_ISLAND_MARKERS)
    if re.search(r"not\s+(an?\s+)?island", q_lower):
        exclusions.update(_ISLAND_MARKERS)

    for match in re.finditer(r"([\u1000-\u109Fa-zA-Z0-9\s]+?)\s*မဟုတ်", q):
        phrase = match.group(1).strip()
        if len(phrase) >= 2:
            exclusions.add(phrase.lower())
            for part in re.findall(r"[\u1000-\u109F]+|[a-zA-Z]{3,}", phrase):
                exclusions.add(part.lower())

    return list(exclusions)


def _query_wants_island(query):
    q = str(query or "").lower()
    if any(x in q for x in ("မဟုတ်", "not ", "wrong")):
        return False
    return any(x in q for x in ("ကျွန်း", "gyun", "island"))


def _score_location_row(query, terms, exclusions, question, answer, tags):
    """Location row တစ်ခုအတွက် relevance score。"""
    haystack = f"{question} {answer} {tags}"
    haystack_lower = haystack.lower()
    score = sum(2 for t in terms if t.lower() in haystack_lower)

    if "delivery fee:" in answer.lower():
        score += 10

    for ex in exclusions:
        ex_lower = str(ex).lower()
        if ex_lower and ex_lower in haystack_lower:
            score -= 80

    mm_match = re.search(r"\(([^)]+)\)", answer)
    mm_name = mm_match.group(1).strip() if mm_match else ""
    township_match = re.search(r"Township:\s*([^|]+)", answer, re.IGNORECASE)
    township = township_match.group(1).strip() if township_match else ""

    for term in terms:
        term_lower = term.lower()
        if mm_name and (term == mm_name or term_lower == mm_name.lower()):
            score += 25
        if township and term_lower == township.lower():
            score += 20

    term_set = {t.lower() for t in terms}
    if "မော်လမြိုင်" in term_set or "mawlamyine" in term_set:
        if _query_wants_island(query):
            if any(m in haystack_lower for m in _ISLAND_MARKERS):
                score += 45
            elif township and township.lower() == "mawlamyine" and "gyun" not in township.lower():
                score -= 25
        else:
            if any(m in haystack_lower for m in _ISLAND_MARKERS):
                score -= 45
            if township.lower() == "mawlamyine" and "gyun" not in township.lower():
                score += 30
            if mm_name == "မော်လမြိုင်":
                score += 20

    if township and "gyun" in township.lower():
        for term in terms:
            if term.lower() == "mawlamyine" and "mawlamyinegyun" not in str(query).lower():
                if "မော်လမြိုင်" in str(query) and "ကျွန်း" not in str(query):
                    score -= 15

    return score


def search_location_delivery(query, user_level):
    """Location table (numeric category ID) မှ delivery fee/COD/days ရှာခြင်း。"""
    if not query or not str(query).strip():
        return None

    terms = _extract_search_tokens(query)
    if not terms:
        return None

    exclusions = _extract_location_exclusions(query)

    conn = get_connection()
    try:
        c = conn.cursor()
        best_row = None
        best_score = 0

        sql = '''SELECT category, question, answer, tags FROM knowledge_base
                 WHERE category GLOB '[0-9]*'
                 AND (question LIKE ? OR answer LIKE ? OR tags LIKE ?)
                 AND level <= ?
                 ORDER BY length(answer) DESC
                 LIMIT 8'''

        seen_cats = set()
        candidates = []

        for term in terms:
            pattern = f"%{term}%"
            c.execute(sql, (pattern, pattern, pattern, user_level))
            for cat, question, answer, tags in c.fetchall():
                if cat in seen_cats:
                    continue
                seen_cats.add(cat)
                score = _score_location_row(query, terms, exclusions, question, answer, tags)
                if score > 0:
                    candidates.append((score, cat, question, answer, tags))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, cat, question, answer, tags = candidates[0]
            best_row = (cat, question, answer, tags)

        if not best_row or best_score < 2:
            return None

        cat, question, answer, tags = best_row
        return f"Location ID: {cat}\n{question}\n{answer}\nTags: {tags}"
    except Exception as e:
        log.error(f"❌ Search Location Delivery Error: {e}")
        return None
    finally:
        conn.close()


def format_location_delivery_reply(location_text):
    """Synced location row ကနေ customer-facing Burmese reply ထုတ်ခြင်း (OS tone)。"""
    import re
    if not location_text:
        return None

    fee_match = re.search(
        r"Delivery Fee:\s*(\d+)\s*MMK\s*\(base\s*(\d+)\s*kg,\s*extra\s*(\d+)\s*MMK per kg\)",
        location_text,
        re.IGNORECASE,
    )
    if not fee_match:
        return None

    base_fee, base_kg, extra_fee = fee_match.group(1), fee_match.group(2), fee_match.group(3)
    mm_name = ""
    mm_match = re.search(r"\(([^)]+)\)", location_text)
    if mm_match:
        mm_name = mm_match.group(1).strip()

    township = re.search(r"Township:\s*([^|]+)", location_text)
    state = re.search(r"State:\s*([^(\n|]+)", location_text)
    home = re.search(r"Home Delivery:\s*(\S+)", location_text, re.IGNORECASE)
    cod = re.search(r"COD:\s*(\S+)", location_text, re.IGNORECASE)
    gate = re.search(r"Gate Drop:\s*(\S+)", location_text, re.IGNORECASE)
    days = re.search(r"Estimated Days:\s*(\d+)", location_text, re.IGNORECASE)
    rider = re.search(r"Rider Name:\s*(.+)", location_text, re.IGNORECASE)
    rider_name = rider.group(1).strip() if rider else ""

    def _is_yes(value):
        return str(value or "").strip().lower() in ("yes", "y", "true", "1")

    home_yes = _is_yes(home.group(1) if home else "")
    cod_yes = _is_yes(cod.group(1) if cod else "")
    gate_yes = _is_yes(gate.group(1) if gate else "")

    state_mm_map = {
        "yangon": "ရန်ကုန်တိုင်း",
        "mon": "မွန်ပြည်နယ်",
        "mandalay": "မန္တလေးတိုင်း",
        "kachin": "ကချင်ပြည်နယ်",
        "kayin": "ကရင်ပြည်နယ်",
        "kayah": "ကယားပြည်နယ်",
        "chin": "ချင်းပြည်နယ်",
        "sagaing": "စစ်ကိုင်းတိုင်း",
        "tanintharyi": "တနင်္သာရီတိုင်း",
        "bago": "ပဲခူးတိုင်း",
        "magway": "မကွေးတိုင်း",
        "ayeyarwady": "ဧရာဝတီတိုင်း",
        "shan": "ရှမ်းပြည်နယ်",
        "naypyidaw": "နေပြည်တော်ပြည်ထောင်စုနယ်မြ",
    }
    state_en = state.group(1).strip() if state else ""
    state_mm = state_mm_map.get(state_en.lower(), state_en)
    township_label = mm_name or (township.group(1).strip() if township else "ဒီနေရာ")
    location_label = f"{state_mm}၊ {township_label}" if state_mm else township_label

    service_parts = []
    if home_yes and cod_yes:
        service_parts.append("အရောက်ပို့ငွေကောက်ပေးခြင်း")
    elif home_yes:
        service_parts.append("အိမ်ရောက်ပို့ပေးခြင်း")
    elif cod_yes:
        service_parts.append("အရောက်ငွေကောက်ပေးခြင်း")

    if gate_yes:
        service_parts.append("ဂိတ်ချပေးခြင်း")

    if not service_parts:
        service_text = "ပို့ဆောင်မှု"
    elif len(service_parts) == 1:
        service_text = service_parts[0]
    else:
        service_text = f"{service_parts[0]} (သို့မဟုတ်) {service_parts[1]}"

    is_royal_express = "royal express" in rider_name.lower()

    if is_royal_express:
        intro = f"{location_label}ကို Royal Express ဖြင့် {service_text} ဝန်ဆောင်မှုရပါတယ်ခင်ဗျ။"
    else:
        intro = f"{location_label}ကို {service_text} ဝန်ဆောင်မှုရပါတယ်ခင်ဗျ။"

    lines = [
        intro,
        f"ပို့ခ: {base_kg} kg ထိ {base_fee} ကျပ်၊ အပို 1 kg လျှင် {extra_fee} ကျပ် ကျသင့်ပါမယ်နော်။",
    ]
    if days:
        lines.append(f"ပစ္စည်းအပ်ရက်မပါပဲ ခန့်မှန်းပို့ရောက်ချိန်: {days.group(1)} ရက်ဝန်းကျင်ပါခင်ဗျ။")

    remarks = re.search(r"Special Remarks:\s*(.+)", location_text, re.IGNORECASE)
    if remarks and remarks.group(1).strip():
        lines.append(f"မှတ်ချက်: {remarks.group(1).strip()}")

    return "\n".join(lines)


def search_knowledge(query, user_level):
    """
    User ရဲ့ Level အလိုက် Database (Google Sheet sync) ထဲမှာ မေးခွန်းရှာပေးခြင်း。
    Multi-token scoring + Myanmar location alias support.
    Level 1 = Customer Inquire sheet | Level 2 = OS | Level 3 = Staff
    """
    if not query or not str(query).strip():
        return None

    conn = get_connection()
    try:
        c = conn.cursor()
        scored_rows = {}
        search_terms = [str(query).strip()]
        search_terms.extend(_extract_search_tokens(query))

        sql = '''SELECT category, question, answer FROM knowledge_base
                 WHERE (question LIKE ? OR answer LIKE ? OR tags LIKE ? OR category LIKE ?)
                 AND level <= ?'''

        for term in search_terms:
            if not term:
                continue
            pattern = f"%{term}%"
            weight = 3 if term == search_terms[0] else 1
            c.execute(sql, (pattern, pattern, pattern, pattern, user_level))
            for cat, q, a in c.fetchall():
                key = (cat, q, a)
                scored_rows[key] = scored_rows.get(key, 0) + weight

        if not scored_rows:
            return None

        ranked = sorted(scored_rows.items(), key=lambda item: item[1], reverse=True)[:12]
        combined_context = ""
        for (cat, q, a), _score in ranked:
            combined_context += f"Category: {cat}\nQuestion: {q}\nAnswer: {a}\n---\n"
        return combined_context
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


def search_delivery_knowledge(query, user_level):
    """
    Delivery-info topic only: location fee rows + general KB snippets (additive).
    Does not replace search_knowledge — combines best matches for /ai grounding.
    """
    if not query or not str(query).strip():
        return None
    parts = []
    try:
        loc = search_location_delivery(query, user_level)
        if loc:
            parts.append(loc)
        kb = search_knowledge(query, user_level)
        if kb and kb not in parts:
            parts.append(kb)
    except Exception as e:
        log.error(f"❌ search_delivery_knowledge error: {e}")
        return None
    if not parts:
        return None
    return "\n---\n".join(parts)


def load_tone_examples(user_level, limit=6):
    """Google Sheet synced OS tone/example snippets for human-like /ai replies."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            '''SELECT category, question, answer FROM knowledge_base
               WHERE (
                   category LIKE '%Tone%'
                   OR category LIKE '%Example%'
                   OR tags LIKE '%tone%'
                   OR tags LIKE '%example%'
               )
               AND level <= ?
               ORDER BY category ASC
               LIMIT ?''',
            (user_level, limit),
        )
        rows = c.fetchall()
        if not rows:
            return ""

        tone_text = "[OS TONE & EXAMPLE SNIPPETS]\n"
        for cat, q, a in rows:
            tone_text += f"Category: {cat}\nExample Q: {q}\nExample A: {a}\n---\n"
        return tone_text
    except Exception as e:
        log.error(f"❌ load_tone_examples error: {e}")
        return ""
    finally:
        conn.close()


def get_status_meaning_map():
    """Customer Inquire 3 > Status Mean — synced knowledge_base lookup."""
    conn = get_connection()
    try:
        c = conn.cursor()
        rows = c.execute(
            """SELECT question, answer FROM knowledge_base
               WHERE (
                   category LIKE '%Status Mean%'
                   OR tags LIKE '%status_mean%'
               )
               AND TRIM(COALESCE(question, '')) != ''
               AND TRIM(COALESCE(answer, '')) != ''"""
        ).fetchall()
        result = {}
        for question, answer in rows:
            key = str(question).strip().upper()
            result[key] = str(answer).strip()
        return result
    except Exception as e:
        log.error(f"❌ get_status_meaning_map error: {e}")
        return {}
    finally:
        conn.close()


def get_remark_meaning_map():
    """Customer Inquire 3 > Remark Mean — synced knowledge_base lookup."""
    conn = get_connection()
    try:
        c = conn.cursor()
        rows = c.execute(
            """SELECT question, answer FROM knowledge_base
               WHERE (
                   category LIKE '%Remark Mean%'
                   OR tags LIKE '%remark_mean%'
               )
               AND TRIM(COALESCE(question, '')) != ''
               AND TRIM(COALESCE(answer, '')) != ''"""
        ).fetchall()
        result = {}
        for question, answer in rows:
            key = str(question).strip().upper()
            result[key] = str(answer).strip()
        return result
    except Exception as e:
        log.error(f"❌ get_remark_meaning_map error: {e}")
        return {}
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

def get_pickup_order_by_msg(orig_msg_id, chat_id):
    """ orig_msg_id နှင့် chat_id ဖြင့် pickup order ကို ရှာဖွေရန် """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?",
            (orig_msg_id, chat_id)
        ).fetchone()
        return res
    finally:
        conn.close()

def upsert_pickup_queue(chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status='PENDING'):
    """ Pickup Queue ထဲသို့ အချက်အလက်များ ထည့်သွင်းခြင်း သို့မဟုတ် ရှိပြီးသားကို Update လုပ်ခြင်း """
    conn = get_connection()
    try:
        # Check if exists
        res = conn.execute("SELECT id FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        if res:
            queue_id = res[0]
            conn.execute(
                "UPDATE pickup_queue SET target_date = ?, os_name = ?, remark = COALESCE(?, remark), vehicle = COALESCE(?, vehicle), status = ? WHERE id = ?",
                (target_date, os_name, remark, vehicle, status, queue_id)
            )
            conn.commit()
            return queue_id
        else:
            cursor = conn.execute(
                "INSERT INTO pickup_queue (chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, int(time.time()))
            )
            conn.commit()
            return cursor.lastrowid
    finally:
        conn.close()

def confirm_pickup_order(queue_id, shop_msg_id=None):
    """ WAITING_CONFIRM ဖြစ်နေသော order ကို PENDING ပြောင်း၍ စက်ရုပ်ကို အလုပ်လုပ်ခိုင်းခြင်း """
    conn = get_connection()
    try:
        if shop_msg_id:
            conn.execute("UPDATE pickup_queue SET status = 'PENDING', shop_msg_id = ? WHERE id = ?", (shop_msg_id, queue_id))
        else:
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
        if status == 'SUCCESS':
            # When marked as SUCCESS, we also want to ensure it's resolved in message_logs
            order = conn.execute("SELECT orig_msg_id, chat_id FROM pickup_queue WHERE id = ?", (queue_id,)).fetchone()
            if order:
                orig_msg_id, chat_id = order
                conn.execute("UPDATE message_logs SET status = 'HANDLED_BY_AI' WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id))
        
        conn.execute(
            "UPDATE pickup_queue SET status = ?, error_msg = ? WHERE id = ?",
            (status, error_msg, queue_id)
        )
        conn.commit()
    finally:
        conn.close()

def update_pickup_status(queue_id, status, error_msg=None):
    """ Alias for update_queue_status (used by execute_pickup_submission) """
    return update_queue_status(queue_id, status, error_msg)

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
def get_stale_pickup_orders():
    """ ယနေ့မတိုင်မီက တင်ထားပြီး WAITING_CONFIRM သို့မဟုတ် PENDING ဖြစ်နေသော order များကို ရှာဖွေရန် """
    conn = get_connection()
    try:
        # လက်ရှိ မြန်မာစံတော်ချိန်အရ ယနေ့ရက်စွဲ၏ အစ (00:00:00) timestamp ကို ယူမည်
        import pytz
        from datetime import datetime
        tz = pytz.timezone('Asia/Yangon')
        today_start = int(datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, status FROM pickup_queue WHERE created_at < ? AND status IN ('WAITING_CONFIRM', 'PENDING', 'WAITING_SETUP')",
            (today_start,)
        ).fetchall()
        return res
    finally:
        conn.close()
def check_existing_pickup(chat_id, target_date):
    """ သတ်မှတ်ထားတဲ့ ရက်စွဲနဲ့ ဆိုင် (Chat ID) အတွက် submitted pickup ရှိမရှိ စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        clean_id = int(str(chat_id).replace("-100", ""))
        res = conn.execute(
            "SELECT 1 FROM pickup_queue WHERE chat_id IN (?, ?) AND target_date = ? "
            "AND status IN ('WAITING_CONFIRM', 'PENDING', 'PROCESSING', 'SUCCESS')",
            (chat_id, clean_id, target_date)
        ).fetchone()
        return res is not None
    finally:
        conn.close()

# --- [ Shop Mapping Helpers ] ---
def _shop_mapping_chat_id_variants(chat_id):
    """All common Telegram supergroup ID formats for shop_mappings lookup."""
    try:
        norm = normalize_bot_chat_id(chat_id)
    except (TypeError, ValueError):
        return (chat_id,)
    short = int(str(norm).replace("-100", "")) if str(norm).startswith("-100") else norm
    variants = []
    for cid in (chat_id, norm, short):
        if cid not in variants:
            variants.append(cid)
    return tuple(variants)


def get_shop_mapping(chat_id):
  conn = get_connection()
  try:
    variants = _shop_mapping_chat_id_variants(chat_id)
    placeholders = ", ".join(["?"] * len(variants))
    res = conn.execute(
      f"""SELECT website_os_name FROM shop_mappings
          WHERE chat_id IN ({placeholders})
            AND TRIM(COALESCE(website_os_name, '')) != ''
          ORDER BY updated_at DESC LIMIT 1""",
      variants,
    ).fetchone()
    return res[0].strip() if res and res[0] else None
  finally:
    conn.close()


def get_group_website_os_name(chat_id):
    """Website OS name for tracking scope — shop_mappings only (not Telegram title)."""
    return get_shop_mapping(chat_id)

def set_shop_mapping(chat_id, website_os_name):
    conn = get_connection()
    try:
        # 💡 Chat ID Mismatch Fix: Always store with -100 prefix for consistency in new mappings
        full_id = chat_id
        if not str(chat_id).startswith("-100"):
            try: full_id = int(f"-100{chat_id}")
            except: pass
            
        conn.execute(
            "INSERT OR REPLACE INTO shop_mappings (chat_id, website_os_name, updated_at) VALUES (?, ?, ?)",
            (full_id, website_os_name, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()

def delete_shop_mapping(chat_id):
    """ သိမ်းထားသော Shop Mapping ကို ဖျက်ခြင်း (Stale mapping cleanup အတွက်) """
    conn = get_connection()
    try:
        clean_id = int(str(chat_id).replace("-100", ""))
        conn.execute("DELETE FROM shop_mappings WHERE chat_id IN (?, ?)", (chat_id, clean_id))
        conn.commit()
        log.info(f"🗑️ Deleted shop mapping for chat {chat_id}")
    finally:
        conn.close()

def get_unified_shop_data():
    """
    Shop Mapping နှင့် Topic ID များကို တစ်ကြောင်းတည်းဖြစ်အောင် စုစည်းထုတ်ပေးခြင်း (GSheet Export အတွက်)
    Returns: list of (chat_id, telegram_name, website_name, pickup_tid, error_tid, finance_tid, updated_at)
    """
    conn = get_connection()
    try:
        # ၁။ အရင်ဆုံး ရှိသမျှ OS Group အားလုံးကို ယူမည်
        shops = conn.execute("SELECT DISTINCT chat_id, shop_name FROM os_groups").fetchall()
        
        # ၂။ Mapping ရှိပြီးသားဆိုင်များကိုပါ ထည့်သွင်းစဉ်းစားမည် (os_groups မှာ မရှိသေးတာမျိုး ဖြစ်နိုင်၍)
        mapping_ids = conn.execute("SELECT chat_id FROM shop_mappings").fetchall()
        all_ids = set()
        for cid, _ in shops:
            all_ids.add(normalize_bot_chat_id(cid))
        for (cid,) in mapping_ids:
            all_ids.add(normalize_bot_chat_id(cid))
        
        results = []
        for cid in all_ids:
            # Telegram Name ယူခြင်း
            name_row = conn.execute("SELECT shop_name FROM os_groups WHERE chat_id = ? LIMIT 1", (cid,)).fetchone()
            tg_name = name_row[0] if name_row else "Unknown Shop"
            
            # Website Mapping ယူခြင်း (short/full format နှစ်မျိုးလုံး ရှာမည်)
            short_id = int(str(cid).replace("-100", "")) if str(cid).startswith("-100") else cid
            map_row = conn.execute(
                "SELECT website_os_name, updated_at FROM shop_mappings WHERE chat_id IN (?, ?) ORDER BY updated_at DESC LIMIT 1",
                (cid, short_id)
            ).fetchone()
            web_name = map_row[0] if map_row else ""
            updated_at = map_row[1] if map_row else 0
            
            # Topics ယူခြင်း (Logic: target_topic_id အပေါ်မူတည်၍ ခွဲခြားမည်)
            # Pickup (1), Finance (35), Error (37)
            pickup_tid = conn.execute("SELECT topic_id FROM os_groups WHERE chat_id = ? AND target_topic_id = 1", (cid,)).fetchone()
            finance_tid = conn.execute("SELECT topic_id FROM os_groups WHERE chat_id = ? AND target_topic_id = 35", (cid,)).fetchone()
            error_tid = conn.execute("SELECT topic_id FROM os_groups WHERE chat_id = ? AND target_topic_id = 37", (cid,)).fetchone()
            
            results.append((
                cid,
                tg_name,
                web_name,
                pickup_tid[0] if pickup_tid else 0,
                error_tid[0] if error_tid else 0,
                finance_tid[0] if finance_tid else 0,
                updated_at
            ))
        return results
    finally:
        conn.close()

def get_all_shop_mappings():
    """ Database ထဲရှိ Mapping အားလုံးကို ဆွဲထုတ်ရန် (GSheet Export အတွက်) """
    return get_unified_shop_data()

def upsert_shop_mappings_batch(data_list):
    """ GSheet မှလာသော Mapping များကို Batch အလိုက် Update လုပ်ရန် """
    conn = get_connection()
    try:
        now = int(time.time())
        # data_list format: [(chat_id, website_os_name), ...]
        formatted_data = []
        for chat_id, website_name in data_list:
            if not chat_id or not website_name: continue
            
            full_id = normalize_bot_chat_id(chat_id)
            formatted_data.append((full_id, website_name.strip(), now))
            
        if formatted_data:
            conn.executemany(
                "INSERT OR REPLACE INTO shop_mappings (chat_id, website_os_name, updated_at) VALUES (?, ?, ?)",
                formatted_data
            )
            conn.commit()
            dedupe_shop_mappings()
            return True
        return False
    finally:
        conn.close()

def update_unified_shop_data(data_list):
    """
    GSheet မှလာသော Unified Data (Mapping + Topics) များကို Database တွင် Update လုပ်ခြင်း
    data_list format: [(chat_id, mapping_id, tg_name, web_name, p_tid, e_tid, f_tid), ...]
    - chat_id: OS Group ID (Column A) → os_groups table
    - mapping_id: Mapping ID (Column B) → shop_mappings table
    """
    conn = get_connection()
    try:
        now = int(time.time())
        c = conn.cursor()
        c.execute("BEGIN TRANSACTION")

        # Normalize + dedupe incoming rows by canonical chat_id
        deduped_rows = {}
        for row in data_list:
            if not row[0]:
                continue
            chat_id = normalize_bot_chat_id(int(row[0]))
            mapping_id = row[1] if len(row) > 1 and row[1] else None
            map_id = normalize_bot_chat_id(int(mapping_id)) if mapping_id else chat_id
            tg_name = row[2] if len(row) > 2 else ""
            web_name = row[3] if len(row) > 3 else ""
            p_tid = row[4] if len(row) > 4 else "0"
            e_tid = row[5] if len(row) > 5 else "0"
            f_tid = row[6] if len(row) > 6 else "0"
            deduped_rows[chat_id] = (chat_id, map_id, tg_name, web_name, p_tid, e_tid, f_tid)
        data_list = list(deduped_rows.values())
        
        # ၁။ Sheet ထဲမှာ ပါလာတဲ့ Chat ID စာရင်းကို ယူထားမည်
        incoming_chat_ids = [row[0] for row in data_list]
        
        # ၂။ Sheet ထဲမှာ မပါတော့တဲ့ ဆိုင်တွေကို ဖျက်မည် (Manual Register မဟုတ်တာတွေကိုပဲ ဖျက်မည်)
        if incoming_chat_ids:
            placeholders = ', '.join(['?'] * len(incoming_chat_ids))
            # os_groups မှ ဖျက်ခြင်း
            c.execute(
                f"DELETE FROM os_groups WHERE chat_id NOT IN ({placeholders}) AND invite_link != 'Manual Register'",
                tuple(incoming_chat_ids)
            )
            # shop_mappings မှ ဖျက်ခြင်း
            c.execute(
                f"DELETE FROM shop_mappings WHERE chat_id NOT IN ({placeholders}) AND chat_id IN (SELECT chat_id FROM os_groups WHERE invite_link != 'Manual Register')",
                tuple(incoming_chat_ids)
            )

        # ၃။ Sheet ထဲမှ data များဖြင့် Update/Insert လုပ်ခြင်း
        for row in data_list:
            chat_id, map_id, tg_name, web_name, p_tid, e_tid, f_tid = row
            if not chat_id:
                continue
            
            # Website Mapping Update (Column B mapping_id + Column A telegram group id)
            store_ids = {map_id, chat_id}
            if not web_name or web_name.strip() == "":
                for sid in store_ids:
                    c.execute("DELETE FROM shop_mappings WHERE chat_id = ?", (sid,))
            else:
                web_clean = web_name.strip()
                for sid in store_ids:
                    c.execute(
                        "INSERT OR REPLACE INTO shop_mappings (chat_id, website_os_name, updated_at) VALUES (?, ?, ?)",
                        (sid, web_clean, now),
                    )
            
            # Topics Update (os_groups)
            # Pickup (Target 1)
            if p_tid and int(p_tid) != 0:
                update_or_insert_os_topic(c, chat_id, tg_name, "Pick Up", int(p_tid), 1)
            else:
                c.execute("DELETE FROM os_groups WHERE chat_id = ? AND target_topic_id = 1", (chat_id,))
            
            # Error (Target 37)
            if e_tid and int(e_tid) != 0:
                update_or_insert_os_topic(c, chat_id, tg_name, "Error", int(e_tid), 37)
            else:
                c.execute("DELETE FROM os_groups WHERE chat_id = ? AND target_topic_id = 37", (chat_id,))
                
            # Finance (Target 35)
            if f_tid and int(f_tid) != 0:
                update_or_insert_os_topic(c, chat_id, tg_name, "Fin & Voc", int(f_tid), 35)
            else:
                c.execute("DELETE FROM os_groups WHERE chat_id = ? AND target_topic_id = 35", (chat_id,))

        conn.commit()
        dedupe_shop_mappings()
        return True
    except Exception as e:
        conn.rollback()
        log.error(f"❌ update_unified_shop_data Error: {e}")
        return False
    finally:
        conn.close()

def update_or_insert_os_topic(cursor, chat_id, shop_name, topic_name, topic_id, target_topic_id):
    """ os_groups ထဲတွင် topic အချက်အလက်များကို update သို့မဟုတ် insert လုပ်ရန် helper """
    # အရင်ရှိမရှိ စစ်မည် (chat_id နှင့် target_topic_id အလိုက်)
    cursor.execute(
        "SELECT 1 FROM os_groups WHERE chat_id = ? AND target_topic_id = ?",
        (chat_id, target_topic_id)
    )
    exists = cursor.fetchone()
    
    target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
    
    if exists:
        cursor.execute(
            "UPDATE os_groups SET topic_id = ?, topic_name = ?, shop_name = ? WHERE chat_id = ? AND target_topic_id = ?",
            (topic_id, topic_name, shop_name, chat_id, target_topic_id)
        )
    else:
        # မရှိသေးလျှင် အသစ်ထည့်မည်
        cursor.execute(
            """INSERT INTO os_groups
               (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id, target_chat_id, target_topic_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, shop_name, chat_id, shop_name, "GSheet Sync", topic_name, topic_id, target_chat, target_topic_id)
        )

def replace_all_shop_mappings(data_list):
    """ Legacy support for gsheet_sync.py """
    return update_unified_shop_data(data_list)

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
    """ Website shops ထဲတွင် အမည်အတိအကျ ရှိမရှိ စစ်ဆေးခြင်း (Case-Insensitive) """
    conn = get_connection()
    try:
        res = conn.execute("SELECT 1 FROM website_shops WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
        return res is not None
    finally:
        conn.close()

def get_unmapped_os_groups():
    """ Mapping မရှိသေးသော OS Group များကို ရှာပေးခြင်း """
    conn = get_connection()
    try:
        # os_groups ထဲမှာရှိပြီး shop_mappings ထဲမှာ မရှိသေးတာတွေကို ယူမည်
        # website_shops ထဲမှာ အတိအကျတူတာ ရှိနေရင်လည်း ကျော်မည်
        # 💡 Chat ID Mismatch Fix
        query = """
            SELECT DISTINCT g.chat_id, g.shop_name
            FROM os_groups g
            LEFT JOIN shop_mappings m ON (g.chat_id = m.chat_id OR CAST('-100' || CAST(g.chat_id AS TEXT) AS INTEGER) = m.chat_id)
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
        allowed_fields = ['target_date', 'vehicle', 'remark', 'status', 'shop_msg_id']
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

def get_manual_register_data():
    """ Manual Register လုပ်ထားပြီး Sheet ထဲ မရောက်သေးသော data များကို ယူခြင်း """
    return get_unified_shop_data_filtered(invite_link='Manual Register')

def get_unified_shop_data_filtered(invite_link=None):
    """ Filter ပါဝင်သော Unified Shop Data ယူခြင်း """
    conn = get_connection()
    try:
        query = "SELECT DISTINCT chat_id, shop_name FROM os_groups"
        if invite_link:
            query += " WHERE invite_link = ?"
            shops = conn.execute(query, (invite_link,)).fetchall()
        else:
            shops = conn.execute(query).fetchall()
            
        results = []
        seen = set()
        for cid, tg_name in shops:
            norm_cid = normalize_bot_chat_id(cid)
            if norm_cid in seen:
                continue
            seen.add(norm_cid)
            # Website Mapping
            short_id = int(str(norm_cid).replace("-100", "")) if str(norm_cid).startswith("-100") else norm_cid
            map_row = conn.execute(
                "SELECT website_os_name, updated_at FROM shop_mappings WHERE chat_id IN (?, ?) ORDER BY updated_at DESC LIMIT 1",
                (norm_cid, short_id)
            ).fetchone()
            web_name = map_row[0] if map_row else ""
            updated_at = map_row[1] if map_row else 0
            
            # Topics
            pickup_tid = conn.execute("SELECT topic_id FROM os_groups WHERE chat_id IN (?, ?) AND target_topic_id = 1 LIMIT 1", (norm_cid, cid)).fetchone()
            finance_tid = conn.execute("SELECT topic_id FROM os_groups WHERE chat_id IN (?, ?) AND target_topic_id = 35 LIMIT 1", (norm_cid, cid)).fetchone()
            error_tid = conn.execute("SELECT topic_id FROM os_groups WHERE chat_id IN (?, ?) AND target_topic_id = 37 LIMIT 1", (norm_cid, cid)).fetchone()
            
            results.append((
                norm_cid, tg_name, web_name,
                pickup_tid[0] if pickup_tid else 0,
                error_tid[0] if error_tid else 0,
                finance_tid[0] if finance_tid else 0,
                updated_at
            ))
        return results
    finally:
        conn.close()

def mark_os_groups_as_synced(chat_ids):
    """ Manual Register မှ GSheet Sync သို့ ပြောင်းလဲခြင်း """
    if not chat_ids: return
    conn = get_connection()
    try:
        placeholders = ', '.join(['?'] * len(chat_ids))
        conn.execute(
            f"UPDATE os_groups SET invite_link = 'GSheet Sync' WHERE chat_id IN ({placeholders}) AND invite_link = 'Manual Register'",
            tuple(chat_ids)
        )
        conn.commit()
    finally:
        conn.close()

def update_os_group_shop_name(chat_id, shop_name):
    """ os_groups ထဲရှိ shop_name ကို update လုပ်ခြင်း (bot.get_chat() မှ title ရပြီးနောက်) """
    if not shop_name or shop_name == "Unknown Shop":
        return
    conn = get_connection()
    try:
        clean_id = int(str(chat_id).replace("-100", ""))
        conn.execute(
            "UPDATE os_groups SET shop_name = ?, group_name = ? WHERE chat_id IN (?, ?) AND (shop_name = 'Unknown Shop' OR shop_name IS NULL OR shop_name = '')",
            (shop_name, shop_name, chat_id, clean_id)
        )
        conn.commit()
    finally:
        conn.close()

def get_pickup_order(queue_id):
    """ queue_id ဖြင့် pickup order အချက်အလက်များကို ယူခြင်း """
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, error_msg, created_at, shop_msg_id FROM pickup_queue WHERE id = ?",
            (queue_id,)
        ).fetchone()
        return res
    finally:
        conn.close()

def get_waiting_confirm_order(chat_id):
    """ လက်ရှိ chat တွင် အတည်ပြုချက်စောင့်ဆိုင်းနေသော (WAITING_CONFIRM သို့မဟုတ် WAITING_SETUP) အော်ဒါရှိမရှိ စစ်ဆေးခြင်း """
    conn = get_connection()
    try:
        clean_id = int(str(chat_id).replace("-100", ""))
        res = conn.execute(
            "SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at "
            "FROM pickup_queue WHERE chat_id IN (?, ?) AND status IN ('WAITING_CONFIRM', 'WAITING_SETUP') "
            "ORDER BY created_at DESC LIMIT 1",
            (chat_id, clean_id)
        ).fetchone()
        return res
    finally:
        conn.close()

# --- [ Version 4.0 Global & Group Settings Helpers ] ---
def get_group_ai_status(chat_id):
    """ Group တစ်ခုချင်းစီ၏ AI Status ကို ရယူခြင်း (Default: ON) """
    conn = get_connection()
    try:
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(chat_id).replace("-100", ""))
        res = conn.execute("SELECT ai_status FROM group_settings WHERE chat_id IN (?, ?)", (chat_id, clean_id)).fetchone()
        return res[0] if res else 'ON'
    finally:
        conn.close()

def set_group_ai_status(chat_id, status):
    """ Group တစ်ခုချင်းစီ၏ AI Status ကို Update လုပ်ခြင်း ('ON' or 'OFF') """
    conn = get_connection()
    try:
        # 💡 Chat ID Mismatch Fix: Always store with -100 prefix
        full_id = chat_id
        if not str(chat_id).startswith("-100"):
            try: full_id = int(f"-100{chat_id}")
            except: pass
            
        conn.execute("INSERT OR REPLACE INTO group_settings (chat_id, ai_status) VALUES (?, ?)", (full_id, status))
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

AI_CONTEXT_TTL_SEC = 3600
AI_CONTEXT_MAX_TURNS = 3


def get_ai_auto_delivery_status():
    """Private chat auto delivery/tracking reply (default OFF — enable after testing)."""
    return get_setting('ai_auto_delivery_reply', 'OFF')


def set_ai_auto_delivery_status(status):
    set_setting('ai_auto_delivery_reply', status)


def append_ai_conversation_turn(
    user_id,
    chat_id,
    query,
    reply_summary,
    topic=None,
    location_id=None,
    location_label=None,
    waybill=None,
):
    """Per (user_id, chat_id) နောက်ဆုံး ၃ turn သိမ်းခြင်း。"""
    import json
    if not user_id or not chat_id:
        return
    now = int(time.time())
    turns = get_ai_conversation_turns(user_id, chat_id, max_age_sec=AI_CONTEXT_TTL_SEC, max_turns=AI_CONTEXT_MAX_TURNS)
    turns.append({
        "query": str(query or "")[:300],
        "reply_summary": str(reply_summary or "")[:400],
        "topic": str(topic or ""),
        "location_id": str(location_id or ""),
        "location_label": str(location_label or ""),
        "waybill": str(waybill or ""),
        "ts": now,
    })
    turns = turns[-AI_CONTEXT_MAX_TURNS:]
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ai_chat_context (user_id, chat_id, turns_json, updated_at) VALUES (?, ?, ?, ?)",
            (int(user_id), int(chat_id), json.dumps(turns, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_ai_conversation_turns(user_id, chat_id, max_age_sec=AI_CONTEXT_TTL_SEC, max_turns=AI_CONTEXT_MAX_TURNS):
    """၁ နာရီအတွင်း နောက်ဆုံး turn များ。"""
    import json
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT turns_json, updated_at FROM ai_chat_context WHERE user_id = ? AND chat_id = ?",
            (int(user_id), int(chat_id)),
        ).fetchone()
        if not res or not res[0]:
            return []
        if max_age_sec and res[1] and (int(time.time()) - int(res[1])) > max_age_sec:
            return []
        try:
            turns = json.loads(res[0])
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(turns, list):
            return []
        cutoff = int(time.time()) - max_age_sec if max_age_sec else 0
        fresh = [t for t in turns if isinstance(t, dict) and int(t.get("ts") or 0) >= cutoff]
        return fresh[-max_turns:]
    finally:
        conn.close()


def get_last_ai_conversation_turn(user_id, chat_id, max_age_sec=AI_CONTEXT_TTL_SEC):
    turns = get_ai_conversation_turns(user_id, chat_id, max_age_sec=max_age_sec, max_turns=AI_CONTEXT_MAX_TURNS)
    return turns[-1] if turns else None


AI_FEEDBACK_REASON_LABELS = {
    "data": "အချက်အလက်မှား",
    "topic": "အကြောင်းအရာလွဲ",
    "tone": "အပြင်လိုတာ",
}


def create_ai_feedback_pending(
    user_id, chat_id, query, reply, topic=None, location_id=None, source_ref=None,
):
    """Sandbox rating — short-lived token for inline keyboard callbacks."""
    import secrets
    conn = get_connection()
    try:
        now = int(time.time())
        conn.execute(
            "DELETE FROM ai_feedback_pending WHERE created_at < ?",
            (now - 86400,),
        )
        token = secrets.token_hex(6)
        conn.execute(
            """INSERT INTO ai_feedback_pending
               (token, user_id, chat_id, query, reply, topic, location_id, source_ref, created_at, completed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                token,
                int(user_id),
                int(chat_id),
                str(query or "")[:500],
                str(reply or "")[:1500],
                str(topic or ""),
                str(location_id or ""),
                str(source_ref or "")[:300],
                now,
            ),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_ai_feedback_pending(token):
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT token, user_id, chat_id, query, reply, topic, location_id, source_ref, completed "
            "FROM ai_feedback_pending WHERE token = ?",
            (str(token),),
        ).fetchone()
        if not res:
            return None
        return {
            "token": res[0],
            "user_id": res[1],
            "chat_id": res[2],
            "query": res[3],
            "reply": res[4],
            "topic": res[5],
            "location_id": res[6],
            "source_ref": res[7],
            "completed": res[8],
        }
    finally:
        conn.close()


def save_ai_feedback_rating(token, user_id, rating, reason=None):
    pending = get_ai_feedback_pending(token)
    if not pending:
        return False, "expired"
    if pending["completed"]:
        return False, "already_rated"
    if int(pending["user_id"]) != int(user_id):
        return False, "not_owner"
    if rating == "down" and reason not in AI_FEEDBACK_REASON_LABELS:
        return False, "need_reason"

    conn = get_connection()
    try:
        now = int(time.time())
        conn.execute(
            """INSERT INTO ai_feedback_logs
               (user_id, chat_id, query, reply, topic, location_id, source_ref, rating, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pending["user_id"],
                pending["chat_id"],
                pending["query"],
                pending["reply"],
                pending["topic"],
                pending["location_id"],
                pending.get("source_ref") or "",
                rating,
                reason or "",
                now,
            ),
        )
        conn.execute(
            "UPDATE ai_feedback_pending SET completed = 1 WHERE token = ?",
            (str(token),),
        )
        conn.commit()
        log.info(
            f"📊 AI feedback saved: {rating} reason={reason or '-'} "
            f"topic={pending['topic']} user={user_id}"
        )
        return True, "ok"
    finally:
        conn.close()


def get_ai_feedback_summary(hours=24):
    """Sandbox QA — recent rating counts."""
    conn = get_connection()
    try:
        since = int(time.time()) - int(hours) * 3600
        rows = conn.execute(
            """SELECT rating, reason, COUNT(*) FROM ai_feedback_logs
               WHERE created_at >= ? GROUP BY rating, reason""",
            (since,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM ai_feedback_logs WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]
        return total, rows
    finally:
        conn.close()


def get_recent_ai_feedback_issues(hours=24, limit=5):
    """နောက်ဆုံး 👎 များ — Sheet ကိုးကားနေရာပါ。"""
    conn = get_connection()
    try:
        since = int(time.time()) - int(hours) * 3600
        return conn.execute(
            """SELECT query, reason, source_ref FROM ai_feedback_logs
               WHERE created_at >= ? AND rating = 'down'
               ORDER BY created_at DESC LIMIT ?""",
            (since, int(limit)),
        ).fetchall()
    finally:
        conn.close()

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
            "UPDATE user_states SET out_of_scope_count = 0, human_intervention_needed = 0, "
            "last_ai_query = NULL, last_location_id = NULL, last_updated = ? WHERE user_id = ?",
            (int(time.time()), user_id)
        )
        conn.commit()
    finally:
        conn.close()


def save_ai_location_context(user_id, query, location_id=None, chat_id=None):
    """Backward-compatible wrapper — use append_ai_conversation_turn instead."""
    if chat_id is None:
        return
    append_ai_conversation_turn(
        user_id, chat_id, query,
        reply_summary=f"location:{location_id or ''}",
        topic="delivery_info",
        location_id=location_id,
    )


def get_ai_location_context(user_id, max_age_sec=3600, chat_id=None):
    """Backward-compatible wrapper."""
    if chat_id is None:
        return None, None
    turn = get_last_ai_conversation_turn(user_id, chat_id, max_age_sec=max_age_sec)
    if not turn:
        return None, None
    return turn.get("query"), turn.get("location_id") or None

def reset_today_pickups(chat_id):
    """
    သတ်မှတ်ထားသော chat_id အတွက် ယနေ့ နှင့် မနက်ဖြန် ရက်စွဲဖြင့် ရှိနေသော pickup များကို ဖျက်ခြင်းနှင့်
    message_logs status ကို PENDING ပြန်ချခြင်း
    """
    conn = get_connection()
    try:
        tz = pytz.timezone('Asia/Yangon')
        now = datetime.now(tz)
        today_str = now.strftime("%d-%m-%Y")
        tomorrow_str = (now + timedelta(days=1)).strftime("%d-%m-%Y")
        
        # ၁။ pickup_queue မှ ယနေ့ နှင့် မနက်ဖြန် record များကို ဖျက်ခြင်း
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(chat_id).replace("-100", ""))
        
        res_queue = conn.execute(
            "DELETE FROM pickup_queue WHERE chat_id IN (?, ?) AND target_date IN (?, ?)",
            (chat_id, clean_id, today_str, tomorrow_str)
        )
        queue_count = res_queue.rowcount
        
        # ၂။ message_logs မှ ယနေ့စာများကို PENDING ပြန်ချခြင်း (HANDLED_BY_AI သို့မဟုတ် SUCCESS ဖြစ်နေလျှင်)
        start_of_day = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        
        res_logs = conn.execute(
            "UPDATE message_logs SET status = 'PENDING', resolved_by = NULL, resolve_time = NULL "
            "WHERE chat_id IN (?, ?) AND timestamp >= ? AND status IN ('HANDLED_BY_AI', 'RESOLVED')",
            (chat_id, clean_id, start_of_day)
        )
        logs_count = res_logs.rowcount
        
        conn.commit()
        log.info(f"♻️ Reset Pickups for {chat_id} ({today_str}, {tomorrow_str}): {queue_count} queue items deleted, {logs_count} logs reset.")
        return queue_count, logs_count
    except Exception as e:
        log.error(f"❌ reset_today_pickups Error: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def check_active_pickup_session(chat_id, minutes=3):
    """
    Check if there's an active pickup session (alert sent or interactive msg sent)
    within the last X minutes to prevent duplicate triggers.
    """
    conn = get_connection()
    try:
        threshold = int(time.time()) - (minutes * 60)
        
        # 1. Check alert_tracking for recent alerts
        # 💡 Chat ID Mismatch Fix
        clean_id = int(str(chat_id).replace("-100", ""))
        
        # 1. Check alert_tracking for recent alerts
        alert = conn.execute(
            "SELECT 1 FROM alert_tracking WHERE chat_id IN (?, ?) AND created_at > ?",
            (chat_id, clean_id, threshold)
        ).fetchone()
        if alert: return True
        
        # 2. Check intermediate messages for recent bot interactions
        inter = conn.execute(
            "SELECT 1 FROM pickup_intermediate_messages WHERE chat_id IN (?, ?) AND created_at > ?",
            (chat_id, clean_id, threshold)
        ).fetchone()
        if inter: return True
        
        return False
    finally:
        conn.close()
