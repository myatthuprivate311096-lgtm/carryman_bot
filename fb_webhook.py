import os
import time
import telebot
import requests
from flask import Flask, request, jsonify
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from logger import log
import db_manager

# Load environment variables
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Configuration
FB_VERIFY_TOKEN = os.getenv('FB_VERIFY_TOKEN', 'my_secret_verify_token')
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TARGET_GROUP_ID = os.getenv('FB_TARGET_GROUP_ID')

# Initialize Telegram Bot (Only for sending, handlers are in main_bot.py)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

app = Flask(__name__)

# Initialize DB Tables
db_manager.init_db()

@app.before_request
def log_request_info():
    log.info(f"🌐 Request: {request.method} {request.url}")
    # log.info(f"Headers: {dict(request.headers)}")

def get_main_keyboard(fb_user_id):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("💬 Chat", callback_data=f"fbc_chat_{fb_user_id}"),
        InlineKeyboardButton("✅ Done", callback_data=f"fbc_done_{fb_user_id}")
    )
    return markup

@app.route('/webhook', methods=['GET'])
def verify():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == FB_VERIFY_TOKEN:
        log.info("✅ Facebook Webhook Verified.")
        return challenge, 200
    return 'Verification failed', 403

@app.route('/privacy', methods=['GET'])
def privacy():
    """
    Privacy Policy page required for Facebook App Review
    """
    return """
    <html>
        <head><title>Privacy Policy - CarryMan Bot</title></head>
        <body style="font-family: sans-serif; padding: 40px; line-height: 1.6;">
            <h1>Privacy Policy</h1>
            <p>Last updated: May 04, 2026</p>
            <p>CarryMan Bot ("we", "our", or "us") operates the Messenger integration to facilitate customer support.</p>
            <h2>1. Data Collection</h2>
            <p>We receive messages and user identifiers (PSID) from Facebook Messenger to forward them to our internal support team via Telegram.</p>
            <h2>2. Data Usage</h2>
            <p>The data is used solely for responding to customer inquiries. We do not sell or share this data with third parties.</p>
            <h2>3. Data Retention</h2>
            <p>Messages are stored temporarily in our database to maintain conversation context and are deleted once the support task is marked as "Done".</p>
            <h2>4. Contact Us</h2>
            <p>If you have any questions about this Privacy Policy, please contact us through our Facebook Page.</p>
        </body>
    </html>
    """, 200

@app.route('/', methods=['GET'])
def index():
    """ Simple status page for Reviewer """
    return "<h1>CarryMan Messenger Integration is Live</h1><p>Status: Operational</p>", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # Debug: Log raw request data
    log.info(f"📩 Received POST request on /webhook")
    data = request.get_json()
    log.info(f"📦 Payload: {data}")

    if data.get('object') == 'page':
        for entry in data.get('entry', []):
            for messaging_event in entry.get('messaging', []):
                if messaging_event.get('message'):
                    sender_id = messaging_event['sender']['id']
                    message_text = messaging_event['message'].get('text', '')
                    attachments = messaging_event['message'].get('attachments', [])
                    
                    # Fetch User Name
                    user_name = get_fb_user_name(sender_id)
                    
                    # Handle Inbound Message
                    handle_fb_message(sender_id, user_name, message_text, attachments)
        return "EVENT_RECEIVED", 200
    return "NOT_FOUND", 404

def handle_fb_message(fb_user_id, fb_user_name, text, attachments):
    try:
        # 1. Check if a staff is already chatting with this user
        active_staff_id = db_manager.get_staff_by_fb_user(fb_user_id)
        if active_staff_id:
            # Forward directly to staff's private chat
            msg = f"🔵 **{fb_user_name}**\n💬 {text}"
            bot.send_message(active_staff_id, msg, parse_mode='Markdown')
            if attachments:
                forward_attachments(active_staff_id, fb_user_name, attachments)
            return

        # 2. Aggregation Logic for Group
        task = db_manager.get_fb_task(fb_user_id)
        
        if task and task['status'] == 'PENDING':
            # Append to existing message
            combined_text = f"{task['last_text']}\n{text}" if text else task['last_text']
            formatted_msg = f"🔵 **Facebook Messenger**\n👤 Name: **{fb_user_name}**\n💬 Message: {combined_text}"
            
            try:
                bot.edit_message_text(formatted_msg, TARGET_GROUP_ID, task['tg_group_msg_id'],
                                     reply_markup=get_main_keyboard(fb_user_id), parse_mode='Markdown')
                db_manager.upsert_fb_task(fb_user_id, fb_user_name, task['tg_group_msg_id'], 'PENDING', combined_text)
            except Exception as e:
                # If edit fails (e.g. message too old), send new one
                send_new_group_task(fb_user_id, fb_user_name, text, attachments)
        else:
            # New task or previously done
            send_new_group_task(fb_user_id, fb_user_name, text, attachments)

    except Exception as e:
        log.error(f"❌ Error in handle_fb_message: {e}")

def send_new_group_task(fb_user_id, fb_user_name, text, attachments):
    formatted_msg = f"🔵 **Facebook Messenger**\n👤 Name: **{fb_user_name}**\n💬 Message: {text}"
    sent_msg = bot.send_message(TARGET_GROUP_ID, formatted_msg,
                               reply_markup=get_main_keyboard(fb_user_id), parse_mode='Markdown')
    db_manager.upsert_fb_task(fb_user_id, fb_user_name, sent_msg.message_id, 'PENDING', text)
    if attachments:
        forward_attachments(TARGET_GROUP_ID, fb_user_name, attachments)

def forward_attachments(chat_id, fb_user_name, attachments):
    for att in attachments:
        url = att.get('payload', {}).get('url')
        if att['type'] == 'image':
            bot.send_photo(chat_id, url, caption=f"🖼 FB Attachment from **{fb_user_name}**")
        else:
            bot.send_message(chat_id, f"📎 FB {att['type']} from **{fb_user_name}**: {url}")

def get_fb_user_name(fb_user_id):
    """ Fetch user name from Facebook Graph API """
    # Use a specific API version for stability
    url = f"https://graph.facebook.com/v19.0/{fb_user_id}?fields=first_name,last_name,name&access_token={FB_PAGE_ACCESS_TOKEN}"
    try:
        log.info(f"🔍 Fetching name for FB User: {fb_user_id}")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            full_name = data.get('name') or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
            if full_name:
                log.info(f"✅ Found FB Name: {full_name}")
                return full_name
        else:
            log.warning(f"⚠️ FB API Name Fetch Failed: {r.status_code} - {r.text}")
    except Exception as e:
        log.error(f"❌ Error fetching FB name: {e}")
    
    return fb_user_id

if __name__ == '__main__':
    log.info("🚀 FB Webhook Server starting on port 5000...")
    app.run(host='0.0.0.0', port=5000)
