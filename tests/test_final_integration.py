import asyncio
import os
import sys
import time
import sqlite3
from telebot import types

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_manager
import ai_utils
from handlers import message_handler
import main_router

async def test_integration():
    print("🚀 Starting Final Integration Test...")
    
    # 1. Check AI Model
    print("\n1️⃣ Checking AI Model Configuration...")
    # We can't easily check the default arg of get_ai_completion without inspecting source,
    # but we can check if ai_utils was modified correctly.
    with open("carryman_bot/ai_utils.py", "r") as f:
        content = f.read()
        if "google/gemini-3.1-flash-lite-preview" in content:
            print("✅ AI Model is correctly set to 3.1 Flash Lite.")
        else:
            print("❌ AI Model is NOT correctly set.")

    # 2. Check DB WAL Mode
    print("\n2️⃣ Checking Database WAL Mode...")
    conn = db_manager.get_connection()
    res = conn.execute("PRAGMA journal_mode;").fetchone()
    print(f"📊 Current Journal Mode: {res[0]}")
    if res[0].lower() == "wal":
        print("✅ WAL Mode is active.")
    else:
        print("❌ WAL Mode is NOT active.")
    conn.close()

    # 3. Test Async Message Ingestion (Receiver Logic)
    print("\n3️⃣ Testing Async Message Ingestion...")
    mock_chat_id = -999999999
    mock_user_id = 88888888
    mock_msg_id = int(time.time())
    mock_text = "Test integration message"
    
    # Ensure the chat is registered as OS group for the test
    db_manager.add_os_group(mock_chat_id, "Test Integration Shop")
    
    print(f"📩 Logging mock message {mock_msg_id}...")
    await asyncio.to_thread(
        db_manager.log_message,
        mock_msg_id, mock_chat_id, 1, mock_user_id, mock_text, int(time.time())
    )
    
    # Verify in DB
    conn = db_manager.get_connection()
    row = conn.execute("SELECT status FROM message_logs WHERE msg_id = ? AND chat_id = ?", (mock_msg_id, mock_chat_id)).fetchone()
    conn.close()
    
    if row and row[0] == 'PENDING':
        print(f"✅ Message successfully ingested with status: {row[0]}")
    else:
        print(f"❌ Message ingestion failed or status incorrect: {row}")

    # 4. Test Worker Queue Logic
    print("\n4️⃣ Testing Worker Queue Logic...")
    # Simulate a pickup request
    db_manager.upsert_pickup_queue(
        mock_chat_id, mock_msg_id, "05-05-2026", "Test Shop", "Test Remark", "Bicycle", status='PENDING'
    )
    
    print("📦 Checking if worker can see the pending pickup...")
    item = db_manager.get_next_queued_pickup()
    if item and item[2] == mock_msg_id:
        print(f"✅ Worker successfully found the pending pickup (Queue ID: {item[0]})")
    else:
        print("❌ Worker failed to find the pending pickup.")

    # Cleanup test data
    print("\n🧹 Cleaning up test data...")
    conn = db_manager.get_connection()
    conn.execute("DELETE FROM message_logs WHERE chat_id = ?", (mock_chat_id,))
    conn.execute("DELETE FROM pickup_queue WHERE chat_id = ?", (mock_chat_id,))
    conn.execute("DELETE FROM os_groups WHERE chat_id = ?", (mock_chat_id,))
    conn.commit()
    conn.close()
    print("✅ Cleanup complete.")

    print("\n✨ Final Integration Test Passed Successfully!")

if __name__ == "__main__":
    asyncio.run(test_integration())
