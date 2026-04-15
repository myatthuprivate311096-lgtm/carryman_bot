# Version: 3.7 (Master DB - 100% Functionally Complete & Synced)
import sqlite3
import time
from datetime import datetime
from logger import log

DB_FILE = 'carryman.db'

def get_connection():
    """ Database ချိတ်ဆက်မှုအား Multithreading နှင့် မြန်နှုန်းမြင့် WAL Mode သတ်မှတ်ခြင်း """
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;') 
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # ၁။ Table များ တည်ဆောက်ခြင်း
    c.execute('CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, branch TEXT, dept TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS os_groups (chat_id INTEGER PRIMARY KEY, shop_name TEXT)')
    c.execute('''CREATE TABLE IF NOT EXISTS message_logs 
                 (msg_id INTEGER, chat_id INTEGER, topic_id INTEGER, user_id INTEGER, 
                  text TEXT, timestamp INTEGER, status TEXT, resolved_by TEXT, resolve_time INTEGER,
                  PRIMARY KEY (msg_id, chat_id))''')
    c.execute('CREATE TABLE IF NOT EXISTS alert_tracking (original_msg_id INTEGER, chat_id INTEGER, alert_msg_id INTEGER, alert_chat_id INTEGER)')
    
    # ၂။ Default Settings ထည့်သွင်းခြင်း
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_active', 'True')")

    # ၃။ 🚨 Migration Engine (Column အသစ်များ အလိုအလျောက် စစ်ဆေးထည့်သွင်းခြင်း)
    migrations = {
        "staff": ["branch TEXT", "dept TEXT"],
        "message_logs": ["text TEXT", "resolved_by TEXT", "resolve_time INTEGER", "topic_id INTEGER"]
    }
    
    for table, columns in migrations.items():
        for col in columns:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            except sqlite3.OperationalError: # Column ရှိနှင့်ပြီးသားဆိုလျှင် ကျော်သွားမည်
                pass
    
    # 'message' column အဟောင်းအား 'text' သို့ နာမည်ပြောင်းရန် (Legacy Support)
    try: c.execute("ALTER TABLE message_logs RENAME COLUMN message TO text")
    except: pass

    conn.commit()
    conn.close()
    log.info("✅ Database (Version 3.7) Initialized & Migrated Successfully.")

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
def log_message(msg_id, chat_id, topic_id, user_id, text, timestamp=None):
    if timestamp is None: timestamp = int(time.time())
    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO message_logs (msg_id, chat_id, topic_id, user_id, text, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')", 
                     (msg_id, chat_id, topic_id, user_id, text, timestamp))
        conn.commit()
    finally: conn.close()

def resolve_message(msg_id, chat_id, staff_name):
    conn = get_connection()
    now = int(time.time())
    try:
        if msg_id != 0:
            conn.execute("UPDATE message_logs SET status='resolved', resolved_by=?, resolve_time=? WHERE msg_id=? AND chat_id=?", 
                         (staff_name, now, msg_id, chat_id))
        else:
            conn.execute("UPDATE message_logs SET status='resolved', resolved_by=?, resolve_time=? WHERE chat_id=? AND status='pending'", 
                         (staff_name, now, chat_id))
        conn.commit()
    finally: conn.close()

# --- [ Watchdog & Context Helpers ] ---
def get_pending_baskets(minutes=15):
    """ Alert မတက်ရသေးသော Pending စာများအား chat_id အလိုက် ဆွဲထုတ်ပေးသည် """
    conn = get_connection()
    threshold = int(time.time()) - (minutes * 60)
    res = conn.execute("SELECT DISTINCT chat_id, topic_id FROM message_logs WHERE status='pending' AND timestamp < ?", (threshold,)).fetchall()
    conn.close()
    return res

def get_topic_context(chat_id, topic_id):
    """ AI Analysis အတွက် မဖြေရသေးသောစာများနှင့် နောက်ဆုံးဖြေထားသောစာ ၅ ကြောင်းကို ယူသည် """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT msg_id, text, timestamp FROM message_logs WHERE chat_id=? AND topic_id=? AND status='pending' ORDER BY timestamp ASC", (chat_id, topic_id))
    pending = c.fetchall()
    c.execute("SELECT text FROM message_logs WHERE chat_id=? AND topic_id=? AND status='resolved' ORDER BY timestamp DESC LIMIT 5", (chat_id, topic_id))
    resolved = [r[0] for r in c.fetchall()]
    c.execute("SELECT shop_name FROM os_groups WHERE chat_id=? LIMIT 1", (chat_id,))
    g_res = c.fetchone()
    shop_name = g_res[0] if g_res else "Unknown Shop"
    conn.close()
    return pending, resolved, shop_name

# --- [ Alert Tracking ] ---
def save_alert_id(original_msg_ids, chat_id, alert_msg_id, alert_chat_id):
    conn = get_connection()
    try:
        for o_id in original_msg_ids:
            conn.execute("INSERT INTO alert_tracking VALUES (?, ?, ?, ?)", (o_id, chat_id, alert_msg_id, alert_chat_id))
        conn.commit()
    finally: conn.close()

def get_linked_alerts(msg_id, chat_id):
    conn = get_connection()
    res = conn.execute("SELECT alert_msg_id, alert_chat_id FROM alert_tracking WHERE (original_msg_id=? OR original_msg_id=0) AND chat_id=?", (msg_id, chat_id)).fetchall()
    conn.close()
    return res

# --- [ OS Group & Analytics ] ---
def check_if_os_group(chat_id):
    conn = get_connection()
    res = conn.execute("SELECT 1 FROM os_groups WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return res is not None

def add_os_group(chat_id, shop_name):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO os_groups VALUES (?, ?)", (chat_id, shop_name))
    conn.commit()
    conn.close()

def get_staff_stats(period="all"):
    conn = get_connection()
    query = "SELECT resolved_by, COUNT(*), AVG((resolve_time - timestamp)/60.0) FROM message_logs WHERE status = 'resolved' AND resolved_by IS NOT NULL "
    if period == "today":
        start_day = int(datetime.now().replace(hour=0, minute=0, second=0).timestamp())
        query += f"AND resolve_time >= {start_day} "
    elif period == "month":
        start_month = int(datetime.now().replace(day=1, hour=0, minute=0, second=0).timestamp())
        query += f"AND resolve_time >= {start_month} "
    query += "GROUP BY resolved_by"
    res = conn.execute(query).fetchall()
    conn.close()
    return res

# Initialize
init_db()