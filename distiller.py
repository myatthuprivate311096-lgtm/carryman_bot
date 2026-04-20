# Version: 1.0 (Daily AI Distiller)
import os
import time
import json
import pytz
from datetime import datetime
from dotenv import load_dotenv
from logger import log
import db_manager
from openai import OpenAI

# 💡 Absolute Path Fix
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY')

def distill_feedback():
    """ နေ့စဉ် Feedback များကို AI ဖြင့် အနှစ်ချုပ်ပြီး Master Rules ထုတ်ပြန်ခြင်း """
    if not GEMINI_API_KEY:
        log.error("❌ AI Key Missing for Distiller")
        return

    log.info("🧠 Starting Daily AI Distiller...")
    
    # ၁။ Feedback ရှိသော Chat/Topic အားလုံးကို ရှာခြင်း
    conn = db_manager.get_connection()
    topics = conn.execute("SELECT DISTINCT chat_id, topic_id FROM feedback_logs").fetchall()
    conn.close()
    
    if not topics:
        log.info("ℹ️ No new feedback to distill.")
        return

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=GEMINI_API_KEY
    )

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

            response = client.chat.completions.create(
                model="google/gemini-2.0-flash-001",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                timeout=60.0
            )
            
            res_data = json.loads(response.choices[0].message.content.strip())
            rules = res_data.get("rules", []) if isinstance(res_data, dict) else res_data
            
            if isinstance(rules, list):
                for rule in rules:
                    db_manager.save_master_rule(chat_id, topic_id, rule)
                
                # ၃။ Cleanup: Process ပြီးသား feedback များကို ဖျက်ခြင်း
                db_manager.clear_processed_feedback(chat_id, topic_id)
                log.info(f"✅ Successfully distilled rules for Topic {topic_id}")
            
        except Exception as e:
            log.error(f"❌ Distiller Error for Topic {topic_id}: {e}")

def run_scheduler():
    """ မနက် ၃ နာရီ (Myanmar Time) တွင် အလုပ်လုပ်မည့် Scheduler """
    log.info("⏰ Distiller Scheduler is running (Target: 03:00 AM MMT)...")
    tz = pytz.timezone('Asia/Yangon')
    
    while True:
        try:
            now = datetime.now(tz)
            # မနက် ၃ နာရီ ဖြစ်မဖြစ် စစ်ဆေးခြင်း
            if now.hour == 3 and now.minute == 0:
                distill_feedback()
                # တစ်မိနစ် စောင့်လိုက်ခြင်းဖြင့် ထပ်ခါထပ်ခါ မ Run စေရန်
                time.sleep(65)
            
            time.sleep(30) # ၃၀ စက္ကန့်တစ်ခါ စစ်မည်
        except Exception as e:
            log.error(f"⚠️ Scheduler Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # စမ်းသပ်ရန်အတွက် တန်း Run ချင်ပါက distill_feedback() ကို ခေါ်နိုင်သည်
    # distill_feedback() 
    run_scheduler()
