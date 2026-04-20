import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auditor
from logger import log

def check_central_topics():
    log.info("🔍 Checking Central Group Topics...")
    CENTRAL_GROUP_ID = -1003601049225
    try:
        # Try to get forum topics
        topics = auditor.bot.get_forum_topics(CENTRAL_GROUP_ID)
        log.info(f"✅ Found {len(topics)} topics in central group")
        for topic in topics:
            log.info(f"   Topic ID: {topic.message_thread_id}, Title: {topic.name}")
    except Exception as e:
        log.error(f"❌ Failed to get forum topics: {e}")
        # Maybe the group is not a forum, try to send a test message without thread
        try:
            msg = auditor.bot.send_message(CENTRAL_GROUP_ID, "Test message without thread")
            log.info(f"✅ Can send to general chat (no thread), message ID: {msg.message_id}")
            auditor.bot.delete_message(CENTRAL_GROUP_ID, msg.message_id)
        except Exception as e2:
            log.error(f"❌ Cannot send to general chat either: {e2}")

if __name__ == "__main__":
    check_central_topics()