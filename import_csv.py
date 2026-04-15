import sqlite3
import csv
import os
from logger import log

CSV_FILE = 'carryman_group_database.csv' 
DB_FILE = 'carryman.db'

def import_os_groups():
    if not os.path.exists(CSV_FILE):
        print(f"❌ Error: {CSV_FILE} ဖိုင်ကို ရှာမတွေ့ပါ။")
        return

    # အစ်ကို့ CSV မှာသုံးထားတဲ့ Header နာမည်များ
    ID_COL = "Group ID"
    NAME_COL = "Group Name"

    # Encoding အမျိုးမျိုးကို စမ်းသပ်ဖတ်ရှုခြင်း
    for encoding in ['utf-8-sig', 'latin-1', 'cp1252']:
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            success_count = 0
            
            with open(CSV_FILE, mode='r', encoding=encoding) as f:
                # CSV format ကို အလိုအလျောက် စစ်ဆေးခြင်း
                dialect = csv.Sniffer().sniff(f.read(1024))
                f.seek(0)
                reader = csv.DictReader(f, dialect=dialect)
                
                for row in reader:
                    try:
                        # Group ID ကို ဂဏန်းအဖြစ် ပြောင်းလဲခြင်း
                        raw_id = str(row[ID_COL]).strip()
                        c_id = int(raw_id)
                        
                        # Group Name ကို ဆိုင်နာမည်အဖြစ် ယူခြင်း
                        s_name = str(row[NAME_COL]).strip()
                        
                        cursor.execute(
                            "INSERT OR IGNORE INTO os_groups (chat_id, shop_name) VALUES (?, ?)", 
                            (c_id, s_name)
                        )
                        success_count += 1
                    except (ValueError, KeyError):
                        continue # Header မဟုတ်သော စာကြောင်းများကို ကျော်သွားမည်

            conn.commit()
            conn.close()
            
            if success_count > 0:
                print(f"✅ အောင်မြင်စွာ သွင်းပြီးပါပြီ! (Encoding: {encoding})")
                print(f"📊 OS Groups စုစုပေါင်း: {success_count} ဆိုင်")
                log.info(f"CSV Import Success: {success_count} groups from {CSV_FILE}")
                return 
            
        except Exception:
            continue

    print("⚠️ ဒေတာများကို သွင်း၍မရပါ။ CSV Header သို့မဟုတ် Format ကို ပြန်စစ်ပေးပါ။")

if __name__ == "__main__":
    import_os_groups()