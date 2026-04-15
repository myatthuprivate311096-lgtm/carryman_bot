import sqlite3

try:
    conn = sqlite3.connect('carryman.db')
    # ပြဿနာတက်နေသော မှတ်တမ်းဇယား အဟောင်းများကို ဖျက်ပစ်မည်
    conn.execute('DROP TABLE IF EXISTS message_logs')
    conn.execute('DROP TABLE IF EXISTS alert_tracking')
    conn.commit()
    conn.close()
    print("✅ ပြဿနာတက်နေသော Database ဇယားဟောင်းများကို အောင်မြင်စွာ ရှင်းလင်းလိုက်ပါပြီ!")
except Exception as e:
    print(f"❌ Error: {e}")