import os
import time
import subprocess
import psutil
import telebot
from telebot import types
import db_manager
import ai_utils
from logger import log
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = -1003601049225
DIAGNOSTIC_TOPIC_ID = 920

bot = telebot.TeleBot(BOT_TOKEN)

def get_system_snapshot():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    uptime = subprocess.check_output(["uptime", "-p"]).decode('utf-8').strip()
    return f"🖥 CPU: {cpu}% | 💾 RAM: {ram}% | ⏱ Uptime: {uptime}"

def check_process_health():
    # Auditor is now a thread inside ingestion, so we only monitor the main process
    processes = ["carryman-ingestion"]
    unhealthy = []
    
    try:
        pm2_output = subprocess.check_output(["pm2", "jlist"]).decode('utf-8')
        import json
        pm2_data = json.loads(pm2_output)
        
        running_names = {proc.get('name'): proc.get('pm2_env', {}).get('status') for proc in pm2_data}
        
        for name in processes:
            status = running_names.get(name)
            if status != 'online':
                unhealthy.append((name, status or "Not Found"))
    except Exception as e:
        log.error(f"Health Monitor PM2 Error: {e}")
        return [("System", f"PM2 Error: {e}")]
        
    return unhealthy

def get_error_logs(process_name, lines=30):
    try:
        output = subprocess.check_output(["pm2", "logs", process_name, "--nostream", "--lines", str(lines)]).decode('utf-8')
        return output
    except:
        return "Could not retrieve logs."

def analyze_error_and_alert(process_name, status, logs):
    prompt = f"""
    Role: Senior Python Developer & System Administrator.
    Task: Analyze the following error logs from a Telegram Bot process ({process_name}) which is currently {status}.
    
    Logs:
    {logs}
    
    Instructions:
    1. Explain the error in simple, human-readable Burmese (Myanmar language).
    2. Generate a 'Fix-Prompt' that the admin can copy-paste back to Roo (an AI coding assistant) to fix the bug. The Fix-Prompt should be in English and very specific.
    3. Suggest which specific system toggle to disable to prevent further errors. Options: 'aioff' (if error is in AI/Auditor), 'pickupoff' (if error is in pickup logic), or 'none' if not applicable.
    
    Output Format:
    Explanation: [Burmese Explanation]
    Fix-Prompt: [English Fix-Prompt]
    Suggested-Action: [aioff/pickupoff/none]
    """
    
    analysis = ai_utils.get_ai_completion(prompt, source='health_monitor')
    if not analysis:
        analysis = "Explanation: AI Analysis failed.\nFix-Prompt: Please check logs manually.\nSuggested-Action: none"

    explanation = "N/A"
    fix_prompt = "N/A"
    suggested_action = "none"
    
    try:
        if "Explanation:" in analysis and "Fix-Prompt:" in analysis:
            parts = analysis.split("Fix-Prompt:")
            explanation = parts[0].replace("Explanation:", "").strip()
            
            if "Suggested-Action:" in parts[1]:
                sub_parts = parts[1].split("Suggested-Action:")
                fix_prompt = sub_parts[0].strip()
                suggested_action = sub_parts[1].strip().lower()
            else:
                fix_prompt = parts[1].strip()
    except Exception as e:
        log.error(f"Error parsing AI analysis: {e}")
        explanation = analysis

    snapshot = get_system_snapshot()
    
    alert_text = (
        f"🚨 **Critical Process Alert: {process_name}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"❌ Status: **{status}**\n"
        f"📊 System: {snapshot}\n\n"
        f"💡 **Explanation (Burmese):**\n{explanation}\n\n"
        f"🛠 **Fix-Prompt:**\n`{fix_prompt}`"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Restart All", callback_data="sys_restart_all"))
    
    if "aioff" in suggested_action:
        markup.add(types.InlineKeyboardButton("🔇 Disable AI (aioff)", callback_data="sys_aioff"))
    elif "pickupoff" in suggested_action:
        markup.add(types.InlineKeyboardButton("⏸️ Disable Pickup (pickupoff)", callback_data="sys_pickupoff"))
        
    markup.add(types.InlineKeyboardButton("📋 Copy Fix-Prompt", callback_data=f"sys_copy_fix"))
    
    # Store the fix prompt in a temporary way or just use the callback to send it
    # For simplicity, we'll use a global-ish state or just rely on the admin copying from the message
    # But the user asked for a button to copy it. Telegram doesn't have a "copy to clipboard" button,
    # so we'll send it as a separate message that's easy to copy.
    
    try:
        bot.send_message(ADMIN_CHAT_ID, alert_text, message_thread_id=DIAGNOSTIC_TOPIC_ID, reply_markup=markup, parse_mode="Markdown")
        # Save fix prompt to a file for the callback to retrieve
        with open(os.path.join(BASE_DIR, 'last_fix_prompt.txt'), 'w', encoding='utf-8') as f:
            f.write(fix_prompt)
    except Exception as e:
        log.error(f"Failed to send health alert: {e}")

def run_monitor():
    log.info("🚀 Proactive Health Monitor started (5 min interval)")
    while True:
        unhealthy = check_process_health()
        if unhealthy:
            for name, status in unhealthy:
                log.warning(f"⚠️ Process {name} is {status}. Analyzing...")
                logs = get_error_logs(name)
                analyze_error_and_alert(name, status, logs)
        
        # Also check for maintenance mode and send persistent alert if just turned on?
        # No, the command handler handles the toggle alert.
        
        time.sleep(300) # 5 minutes

if __name__ == "__main__":
    run_monitor()
