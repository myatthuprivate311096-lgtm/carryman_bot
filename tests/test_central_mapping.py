import os
import time
import sys

# 💡 Absolute Path Fix for Test Script
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db_manager
from modules import auditor
from logger import log

def run_test():
    log.info("🧪 Starting Central Mapping Test (Updated for Explicit Routing)...")
    
    # 1. Setup Test Data for different topics
    test_chat_id = -1003539520778
    CENTRAL_GROUP_ID = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
    
    # We need to make sure these topics exist in os_groups for this chat_id
    conn = db_manager.get_connection()
    try:
        # Clear old entries for this test chat
        conn.execute("DELETE FROM os_groups WHERE chat_id = ?", (test_chat_id,))
        
        # Insert test topics with explicit routing
        # format: (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id, target_chat_id, target_topic_id)
        topics = [
            (test_chat_id, "AI Testing OS Gp", test_chat_id, "AI Testing OS Gp", "Link", "Error", 101, CENTRAL_GROUP_ID, 37),
            (test_chat_id, "AI Testing OS Gp", test_chat_id, "AI Testing OS Gp", "Link", "Pick Up/Urgent", 102, CENTRAL_GROUP_ID, 1),
            (test_chat_id, "AI Testing OS Gp", test_chat_id, "AI Testing OS Gp", "Link", "Fin & Voc", 103, CENTRAL_GROUP_ID, 35),
            (test_chat_id, "AI Testing OS Gp", test_chat_id, "AI Testing OS Gp", "Link", "စုံစမ်းရန်", 104, CENTRAL_GROUP_ID, 1)
        ]
        conn.executemany(
            "INSERT INTO os_groups (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id, target_chat_id, target_topic_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            topics
        )
        conn.commit()
        log.info("✅ Setup test topics in os_groups with explicit routing")
    finally:
        conn.close()

    # 2. Test Routing Logic
    test_cases = [
        (101, "Error", 37),
        (102, "Pick Up", 1),
        (103, "Finance", 35),
        (104, "စုံစမ်းရန်", 1)
    ]
    
    for t_id, label, expected_topic in test_cases:
        target_chat, target_topic = auditor.get_routing_data(test_chat_id, t_id)
        log.info(f"🔍 Testing {label} Topic (ID: {t_id}) -> Routed to Chat: {target_chat}, Topic: {target_topic}")
        
        if target_chat == CENTRAL_GROUP_ID:
            if target_topic == expected_topic:
                log.info(f"✅ {label} Routing is CORRECT")
            else:
                log.error(f"❌ {label} Routing Topic ID is WRONG (Got {target_topic}, Expected {expected_topic})")
        else:
            log.error(f"❌ {label} Routing Chat ID is WRONG (Got {target_chat})")

    # 3. Test Alert Sending with Button
    log.info("🚀 Testing Alert Sending with View Message Button...")
    # We'll test with Error topic
    test_msg_id = 999999
    test_text = "CENTRAL MAPPING TEST: Error in order #789"
    
    # Mocking send_new_alert call
    alert_id = auditor.send_new_alert(test_chat_id, 101, test_msg_id, test_text, "Test Error Summary", "AI Testing OS Gp", int(time.time()))
    if alert_id:
        log.info(f"✅ SUCCESS: Central Alert sent! Alert ID: {alert_id}")
    else:
        log.error("❌ FAILED: Central Alert could not be sent.")

if __name__ == "__main__":
    run_test()
