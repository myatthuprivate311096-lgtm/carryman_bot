# Version: 1.1 (Daily AI Distiller - Module Standardized)
import os
import time
import json
import pytz
import sys
from datetime import datetime
from dotenv import load_dotenv
import ai_utils

# 💡 Absolute Path Fix for Module
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from logger import log
import db_manager

load_dotenv(os.path.join(BASE_DIR, '.env'))

def distill_feedback():
    """ နေ့စဉ် Feedback များကို AI ဖြင့် အနှစ်ချုပ်ပြီး Master Rules ထုတ်ပြန်ခြင်း """
    log.info("🧠 Starting Daily AI Distiller...")
    
    # ၁။ Feedback ရှိသော Chat/Topic အားလုံးကို ရှာခြင်း
    conn = db_manager.get_connection()
    topics = conn.execute("SELECT DISTINCT chat_id, topic_id FROM feedback_logs").fetchall()
    conn.close()
    
    if not topics:
        log.info("ℹ️ No new feedback to distill.")
        # Export existing rules to JSON even if no new feedback
        export_rules_to_json()
        return

    for chat_id, topic_id in topics:
        try:
            feedbacks = db_manager.get_isolated_feedback(chat_id, topic_id, limit=100)
            if not feedbacks: continue

            log.info(f"📊 Distilling {len(feedbacks)} feedbacks for Chat {chat_id}, Topic {topic_id}")
            
            # Context တည်ဆောက်ခြင်း
            feedback_context = "\n".join([f"- [{cat}]: {txt}" for cat, txt in feedbacks])
            
            prompt = f"""
            Role: Senior AI Optimization Engineer.
            Task: Analyze the following 'Wrong Alert' feedback logs from a Telegram Auditor Bot.
            Goal: Create concise, actionable 'Master Rules' to prevent these false positives in the future.
            
            [Feedback Logs for Chat {chat_id}, Topic {topic_id}]:
            {feedback_context}
            
            Rules for Output:
            1. Be extremely concise (e.g., "Do not alert on messages containing only greetings like 'Mingalarpar'").
            2. Focus on patterns (e.g., "Ignore short 'Ok' or 'Thanks' replies from customers").
            3. Output ONLY a JSON array of strings.
            
            Example Output:
            ["Ignore greetings", "Do not alert on 'Ok' only messages"]
            """

            content = ai_utils.get_ai_completion(prompt, response_format={"type": "json_object"}, timeout=60.0)
            if not content:
                continue

            res_data = json.loads(content)
            rules = res_data.get("rules", []) if isinstance(res_data, dict) else res_data
            
            if isinstance(rules, list):
                now_ts = int(time.time())
                for rule in rules:
                    db_manager.save_master_rule(rule, chat_id, topic_id)
                
                # ၃။ Cleanup: Process ပြီးသား feedback များကို ဖျက်ခြင်း
                db_manager.clear_processed_feedback(chat_id, topic_id, now_ts)
                log.info(f"✅ Successfully distilled rules for Topic {topic_id}")
            
        except Exception as e:
            log.error(f"❌ Distiller Error for Topic {topic_id}: {e}")

    # ၄။ Export all rules to JSON for AI context
    export_rules_to_json()

def export_rules_to_json():
    """ Master Rules အားလုံးကို ai_learning_context.json သို့ ထုတ်ယူသိမ်းဆည်းခြင်း """
    try:
        log.info("📂 Exporting Master Rules to JSON...")
        conn = db_manager.get_connection()
        # Get all rules grouped by chat/topic
        rows = conn.execute("SELECT chat_id, topic_id, rule_content FROM master_rules ORDER BY created_at DESC").fetchall()
        conn.close()

        rules_map = {}
        for chat_id, topic_id, rule in rows:
            key = f"{chat_id}_{topic_id}"
            if key not in rules_map:
                rules_map[key] = []
            if rule not in rules_map[key]:
                rules_map[key].append(rule)

        json_path = os.path.join(BASE_DIR, "ai_learning_context.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(rules_map, f, ensure_ascii=False, indent=4)
        
        log.info(f"✅ Exported rules to {json_path}")
    except Exception as e:
        log.error(f"❌ Export Rules Error: {e}")

def run_scheduler():
    """ မနက် ၃:၃၀ နာရီ (Myanmar Time) တွင် အလုပ်လုပ်မည့် Scheduler """
    log.info("⏰ Distiller Scheduler is running (Target: 03:30 AM MMT)...")
    tz = pytz.timezone('Asia/Yangon')
    
    while True:
        try:
            now = datetime.now(tz)
            # မနက် ၃:၃၀ နာရီ ဖြစ်မဖြစ် စစ်ဆေးခြင်း
            if now.hour == 3 and now.minute == 30:
                distill_feedback()
                # တစ်မိနစ် စောင့်လိုက်ခြင်းဖြင့် ထပ်ခါထပ်ခါ မ Run စေရန်
                time.sleep(65)
            
            time.sleep(30) # ၃၀ စက္ကန့်တစ်ခါ စစ်မည်
        except Exception as e:
            log.error(f"⚠️ Scheduler Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_scheduler()
