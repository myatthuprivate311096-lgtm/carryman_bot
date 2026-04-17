# Version: 3.7 (Master DB - 100% Functionally Complete & Synced)
import sqlite3
import time
from datetime import datetime
from logger import log

DB_FILE = 'carryman.db'

def clean_shop_name(raw_name):
    """Shop name မှ emoji/non-ascii noise များကိုဖြတ်၍ base name ပြန်ပေးမည်"""
    if raw_name is None:
        return "Unknown Shop"
    base = str(raw_name).split('🤝')[0]
    ascii_only = base.encode('ascii', 'ignore').decode('utf-8')
    cleaned = " ".join(ascii_only.split()).strip()
    return cleaned or "Unknown Shop"

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
    c.execute('''CREATE TABLE IF NOT EXISTS os_groups (
                 chat_id INTEGER,
                 shop_name TEXT,
                 group_id INTEGER,
                 group_name TEXT,
                 invite_link TEXT,
                 topic_name TEXT,
                 topic_id INTEGER
               )''')
    c.execute('''CREATE TABLE IF NOT EXISTS message_logs 
                 (msg_id INTEGER, chat_id INTEGER, topic_id INTEGER, user_id INTEGER, 
                  text TEXT, timestamp INTEGER, status TEXT, resolved_by TEXT, resolve_time INTEGER,
                  PRIMARY KEY (msg_id, chat_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS alert_tracking (
                 original_msg_id INTEGER,
                 chat_id INTEGER,
                 alert_msg_id INTEGER,
                 alert_chat_id INTEGER,
                 created_at INTEGER
               )''')
    c.execute('''CREATE TABLE IF NOT EXISTS routing_table (
                 os_chat_id INTEGER,
                 os_topic_id INTEGER,
                 alert_chat_id INTEGER,
                 alert_topic_id INTEGER,
                 department_name TEXT,
                 PRIMARY KEY (os_chat_id, os_topic_id)
               )''')
    
    # os_groups schema ကို multi-topic support အတွက် migration လုပ်ခြင်း
    c.execute("PRAGMA table_info(os_groups)")
    os_cols = c.fetchall()
    os_col_names = [col[1] for col in os_cols]
    chat_id_is_pk = any(col[1] == "chat_id" and col[5] == 1 for col in os_cols)

    if chat_id_is_pk:
        c.execute('''CREATE TABLE IF NOT EXISTS os_groups_new (
                     chat_id INTEGER,
                     shop_name TEXT,
                     group_id INTEGER,
                     group_name TEXT,
                     invite_link TEXT,
                     topic_name TEXT,
                     topic_id INTEGER
                   )''')
        c.execute('''INSERT INTO os_groups_new
                     (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id)
                     SELECT chat_id, shop_name, chat_id, shop_name, 'Legacy', 'Legacy', 0 FROM os_groups''')
        c.execute("DROP TABLE os_groups")
        c.execute("ALTER TABLE os_groups_new RENAME TO os_groups")
    else:
        os_group_migrations = {
            "group_id": "INTEGER",
            "group_name": "TEXT",
            "invite_link": "TEXT",
            "topic_name": "TEXT",
            "topic_id": "INTEGER"
        }
        for col_name, col_type in os_group_migrations.items():
            if col_name not in os_col_names:
                try:
                    c.execute(f"ALTER TABLE os_groups ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass

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

    # alert_tracking created_at migration
    try:
        c.execute("ALTER TABLE alert_tracking ADD COLUMN created_at INTEGER")
    except sqlite3.OperationalError:
        pass

    # ၄။ Performance Indexes (Read Speed Optimization)
    c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_status_time ON message_logs(status, timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_message_logs_chat_topic_status ON message_logs(chat_id, topic_id, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_alert_tracking_chat_time ON alert_tracking(chat_id, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_os_groups_chat_topic ON os_groups(chat_id, topic_id)")

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

def resolve_message(msg_id, chat_id, staff_name, method='Reply'):
    conn = get_connection()
    now = int(time.time())
    full_staff_info = f"{staff_name} ({method})"
    try:
        if msg_id != 0:
            conn.execute("UPDATE message_logs SET status='resolved', resolved_by=?, resolve_time=? WHERE msg_id=? AND chat_id=?",
                         (full_staff_info, now, msg_id, chat_id))
        else:
            conn.execute("UPDATE message_logs SET status='resolved', resolved_by=?, resolve_time=? WHERE chat_id=? AND status IN ('pending', 'alerted', 'escalated')",
                         (full_staff_info, now, chat_id))
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
    shop_name = clean_shop_name(g_res[0]) if g_res else "Unknown Shop"
    conn.close()
    return pending, resolved, shop_name

# --- [ Alert Tracking ] ---
def save_alert_id(original_msg_ids, chat_id, alert_msg_id, alert_chat_id):
    conn = get_connection()
    try:
        created_at = int(time.time())
        for o_id in original_msg_ids:
            conn.execute(
                "INSERT INTO alert_tracking (original_msg_id, chat_id, alert_msg_id, alert_chat_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (o_id, chat_id, alert_msg_id, alert_chat_id, created_at)
            )
        conn.commit()
    finally: conn.close()

def get_linked_alerts(msg_id, chat_id):
    conn = get_connection()
    res = conn.execute("SELECT alert_msg_id, alert_chat_id FROM alert_tracking WHERE (original_msg_id=? OR original_msg_id=0) AND chat_id=?", (msg_id, chat_id)).fetchall()
    conn.close()
    return res

def get_routing_entry(chat_id, topic_id):
    """routing_table ထဲမှ os_chat/topic နှင့် ကိုက်ညီသော route ကို ပြန်ပေးမည်"""
    conn = get_connection()
    try:
        res = conn.execute(
            "SELECT alert_chat_id, alert_topic_id FROM routing_table WHERE os_chat_id=? AND os_topic_id=? LIMIT 1",
            (chat_id, topic_id)
        ).fetchone()
        return res
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
        "INSERT INTO os_groups (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, clean_name, chat_id, clean_name, "Legacy", "Legacy", 0)
    )
    conn.commit()
    conn.close()

def save_manual_register_with_routes(chat_id, shop_name, topic_entries, alert_chat_id):
    """
    topic_entries = [
      {"topic_name": "...", "topic_id": 12, "route_topic_id": 1, "department_name": "..."},
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
            route_topic_id = int(item["route_topic_id"])
            dept_name = item["department_name"]

            c.execute(
                """INSERT INTO os_groups
                   (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, clean_name, chat_id, clean_name, "Manual Register", t_name, t_id)
            )

            c.execute(
                """INSERT OR REPLACE INTO routing_table
                   (os_chat_id, os_topic_id, alert_chat_id, alert_topic_id, department_name)
                   VALUES (?, ?, ?, ?, ?)""",
                (chat_id, t_id, alert_chat_id, route_topic_id, dept_name)
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
            WHERE m.status='pending' 
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