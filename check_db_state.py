import db_manager
import json
from logger import log

def check_msg():
    msg_id = 381
    chat_id = -1003539520778
    
    conn = db_manager.get_connection()
    row = conn.execute("SELECT * FROM message_logs WHERE msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    
    if row:
        print(f"Message Data: {row}")
    else:
        print("Message not found in DB")
        
    tracking = conn.execute("SELECT * FROM alert_tracking WHERE original_msg_id = ? AND chat_id = ?", (msg_id, chat_id)).fetchone()
    print(f"Alert Tracking: {tracking}")
    
    route = db_manager.get_routing_entry(chat_id, 1) # Topic 1
    print(f"Routing for Topic 1: {route}")
    
    global_alert = db_manager.get_alert_system_global_status()
    print(f"Global Alert Status: {global_alert}")
    
    conn.close()

if __name__ == "__main__":
    check_msg()
