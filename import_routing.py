import sqlite3
import csv
import os

# ==========================================
# ⚙️ အစ်ကိုပေးထားတဲ့ Link များမှ ID များ (Mapping)
# ==========================================
ALERT_GROUPS = {
    "Error": {"alert_chat_id": -1003601049225, "alert_topic_id": 37}, 
    "Fin & Voc": {"alert_chat_id": -1003601049225, "alert_topic_id": 35},
    "Pick Up": {"alert_chat_id": -1003601049225, "alert_topic_id": 1}
}

def setup_and_import():
    print("⏳ CSV မှ အချက်အလက်အားလုံးကို Routing Table ထဲသို့ ကူးယူနေပါသည်...")
    
    csv_file = 'carryman_group_database.csv'
    if not os.path.exists(csv_file):
        print(f"⚠️ '{csv_file}' ကို ရှာမတွေ့ပါ။ ဖိုင်ကို အရင်ဆုံး တင်ပေးပါ (Upload)။")
        return

    conn = sqlite3.connect('carryman.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS routing_table (
            os_chat_id INTEGER,
            os_topic_id INTEGER,
            alert_chat_id INTEGER,
            alert_topic_id INTEGER,
            department_name TEXT,
            UNIQUE(os_chat_id, os_topic_id)
        )
    ''')

    success_count = 0
    # 💡 ဤနေရာတွင် encoding='utf-8-sig' ဟု ပြောင်းလဲလိုက်ပါသည် (BOM Error ကျော်ရန်)
    with open(csv_file, 'r', encoding='utf-8-sig', errors='ignore') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 5: continue 
            
            try:
                if row[1].lower() in ['group_id', 'chat_id', '']: continue
                    
                os_chat_id = int(row[1])
                topic_name = row[3].strip()
                topic_id = int(row[4])
                
                target_alert = None
                for key in ALERT_GROUPS:
                    if key.lower() in topic_name.lower():
                        target_alert = ALERT_GROUPS[key]
                        break
                
                if target_alert:
                    c.execute('''
                        INSERT OR REPLACE INTO routing_table 
                        (os_chat_id, os_topic_id, alert_chat_id, alert_topic_id, department_name)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (os_chat_id, topic_id, target_alert['alert_chat_id'], target_alert['alert_topic_id'], topic_name))
                    success_count += 1
            except Exception:
                continue

    conn.commit()
    conn.close()
    print(f"✅ လုပ်ငန်းစဉ် ပြီးဆုံးပါပြီ!")
    print(f"📊 စုစုပေါင်း Topic ({success_count}) ခုကို Alert System နှင့် ချိတ်ဆက်ပေးပြီးပါပြီ။")

if __name__ == "__main__":
    setup_and_import()