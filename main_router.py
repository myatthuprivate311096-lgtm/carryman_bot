# Version: 1.0 (Central AI Router & Sandbox Logic)
import os
import json
import importlib
import db_manager
from dotenv import load_dotenv
from logger import log
import ai_utils

# 💡 Absolute Path Fix
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

SANDBOX_CHAT_ID = -1003539520778

def is_ai_office_hours():
    """ AI Auto-Answer အလုပ်လုပ်မည့်အချိန် (09:00 AM - 08:00 PM) """
    import pytz
    from datetime import datetime
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    return 9 <= now.hour < 20

def handle_ai_query(bot, message, is_automatic=False):
    """
    Smart AI Support Logic (DB -> Maps -> AI)
    """
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        is_private = chat_id > 0

        # ၁။ User Level သတ်မှတ်ခြင်း
        user_level = db_manager.get_user_level(user_id, chat_id)

        # --- Private Chat Logic (Staff Exemption & Three-Strike Rule) ---
        if is_private:
            # Check if human intervention is already needed
            _, human_needed = db_manager.get_user_state(user_id)
            if human_needed:
                log.info(f"🔇 AI Muted for user {user_id} due to human_intervention_needed")
                return

            # Staff Exemption (Level 3/4)
            if user_level >= 3:
                log.info(f"👑 Staff Exemption: Full AI access for user {user_id}")
                # Proceed to general AI response without RAG restrictions if needed,
                # but for now we'll just let them pass the out-of-scope check.
            else:
                # Non-Staff: Apply Strict RAG & Three-Strike Rule
                pass # Will be handled during intent/scope check

        # ၂။ မေးခွန်းကို ရယူခြင်း
        query = message.text.replace('/ai', '').strip() if not is_automatic else (message.text or message.caption or '')
        if not query and message.reply_to_message:
            query = message.reply_to_message.text or message.reply_to_message.caption
            
        if not query:
            bot.reply_to(message, "💡 **ဘယ်လိုကူညီပေးရမလဲခင်ဗျာ?**\n\nမေးခွန်းကို တွဲရိုက်ပါ (သို့) မေးလိုသောစာကို Reply ပြန်ပြီး `/ai` လို့ ရိုက်ပါ။")
            return

        log.info(f"🤖 AI Query from {user_id}: {query[:50]}...")

        # ၃။ Step 1: Database (Knowledge Base) Search
        kb_result = db_manager.search_knowledge(query, user_level)
        kb_context = ""
        if kb_result:
            category, question, answer = kb_result
            kb_context = f"\n[Knowledge Base Data]:\nCategory: {category}\nQuestion: {question}\nAnswer: {answer}\n"

        # ၄။ Step 2: AI Intent Detection (Address vs General)
        intent_prompt = f"""
        Analyze the user query and decide if they are asking for the office address, location, or how to get there.
        User Query: "{query}"
        
        Output ONLY 'address' if they ask for location/address.
        Output 'general' for anything else.
        """
        
        intent = ai_utils.get_ai_completion(intent_prompt, timeout=15.0)
        if not intent:
            bot.reply_to(message, "😔 စိတ်မရှိပါနဲ့ခင်ဗျာ။ AI စနစ် ခေတ္တချို့ယွင်းနေလို့ပါ။")
            return
        intent = intent.strip().lower()

        if 'address' in intent:
            # Google Maps Link Logic
            maps_link = "https://maps.app.goo.gl/CarryManLocationPlaceholder" # အစ်ကို့ရဲ့ တကယ့် Link ထည့်ပေးရပါမယ်
            address_text = (
                "📍 **CarryMan Office Location**\n\n"
                "ကျွန်တော်တို့ရုံးချုပ် လိပ်စာမှာ အောက်ပါအတိုင်းဖြစ်ပါတယ်ခင်ဗျာ-\n"
                "🏢 အမှတ် (၁၂၃)၊ လမ်း ၄၀၊ ရန်ကုန်မြို့။\n\n"
                f"🗺 **Google Maps Link:**\n{maps_link}"
            )
            bot.reply_to(message, address_text, parse_mode="Markdown")
            return

        # ၅။ Step 3: General AI Response (with Scope Check for Private Chat)
        is_staff = user_level >= 3
        
        scope_check_prompt = ""
        if is_private and not is_staff:
            scope_check_prompt = """
            Scope Check: 'Determine if the user query is related to CarryMan Logistics services (delivery, tracking, pickup, pricing, locations).
            If it is OUT OF SCOPE (e.g., coding, math, general knowledge, personal questions), output ONLY the word "OUT_OF_SCOPE".
            Otherwise, proceed with the answer.'
            """

        ai_prompt = f"""
        Strict Persona & Tone: 'You are an Online Shop (OS) admin. You MUST strictly follow the tone, style, and examples provided in the OS Tone_&_Example data. Keep answers short, direct, and natural. NEVER use generic AI fluff like "Welcome to...", "If you need more info...", or "I am an AI assistant".'

        {scope_check_prompt}

        Comprehensive Data Extraction: 'When a user asks about delivery to a specific location (e.g., မြစ်ကြီးနား), you MUST look up that location in the database/sheets and extract ALL relevant details. Your answer MUST include:
        - Whether Home Delivery is available.
        - The Delivery Fee range (Min and Max price).
        - Whether COD (Cash on Delivery) is accepted.
        - Estimated Delivery Duration (Days).'

        Format Constraint: 'Combine these details into a single, concise, human-like paragraph in Burmese.'

        [Context Data]:
        {kb_context}

        User Query: "{query}"
        """
        
        answer = ai_utils.get_ai_completion(ai_prompt, timeout=30.0)

        # --- Three-Strike Rule Implementation ---
        if is_private and not is_staff and answer and "OUT_OF_SCOPE" in answer:
            count = db_manager.increment_out_of_scope(user_id)
            
            if count < 3:
                strike_msg = "တောင်းပန်ပါတယ်ခင်ဗျာ။ ကျွန်တော်က CarryMan Logistics နှင့် သက်ဆိုင်သော ဝန်ဆောင်မှုများကိုသာ ဖြေကြားပေးနိုင်ပါတယ်။ အခြားအကြောင်းအရာများကို မဖြေကြားနိုင်ပါဘူးခင်ဗျာ။"
                bot.reply_to(message, strike_msg)
                return
            else:
                # Strike 3
                # To User
                bot.reply_to(message, "admin ဆီကိုအကြောင်းကြားပေးထားတာမို့ ရုံးချိန်အတွင်းအမြန်ဆုံးဆက်သွယ်ပေးပါလိမ့်မယ်နော်")
                
                # To Admin Topic 920
                admin_chat = -1003601049225
                topic_id = 920
                username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"
                alert_text = f"⚠️ **Human Support Needed!**\nA user ({username}) has asked out-of-scope questions 3 times in Private Chat. AI auto-reply is now paused for them."
                
                try:
                    bot.send_message(admin_chat, alert_text, message_thread_id=topic_id)
                except Exception as ae:
                    log.error(f"❌ Failed to send Strike 3 alert to admin: {ae}")
                    # Fallback to general if topic fails
                    bot.send_message(admin_chat, alert_text)

                # Mute Action
                db_manager.set_human_intervention(user_id, 1)
                log.info(f"🚫 User {user_id} muted after 3 strikes.")
                return
        if not answer:
            bot.reply_to(message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")
            return
        
        # 🔇 AI Off Button (Phase 2)
        from telebot import types
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔇 AI ပိတ်မည်", callback_data=f"mute_ai:{message.chat.id}"))
        
        # Split message if too long (Telegram limit 4096)
        full_response = f"🤖 **CarryMan AI Agent**\n\n{answer}"
        if len(full_response) > 4000:
            parts = [full_response[i:i+4000] for i in range(0, len(full_response), 4000)]
            for idx, part in enumerate(parts):
                if idx == 0:
                    bot.reply_to(message, part, reply_markup=markup)
                else:
                    bot.send_message(message.chat.id, part)
        else:
            bot.reply_to(message, full_response, reply_markup=markup)

    except Exception as e:
        log.error(f"❌ AI Query Error: {e}")
        bot.reply_to(message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")

def route_message(bot, message):
    """
    AI မှ Message ကို ဖတ်ပြီး သက်ဆိုင်ရာ Module ဆီသို့ လမ်းကြောင်းပြောင်းပေးခြင်း
    """
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        text = message.text or message.caption
        
        if not text:
            return

        # ၁။ Global & Group Status စစ်ဆေးခြင်း (Phase 2)
        global_ai = db_manager.get_ai_global_status()
        group_ai = db_manager.get_group_ai_status(chat_id)
        global_pickup = db_manager.get_auto_pickup_global_status()
        
        is_sandbox = (chat_id == SANDBOX_CHAT_ID)
        
        if is_sandbox:
            log.info(f"🧪 Sandbox Mode: Bypassing all global/group/time restrictions for chat {chat_id}")
            # Force all toggles to ON for sandbox
            global_ai = 'ON'
            group_ai = 'ON'
            global_pickup = 'ON'
        
        log.info(f"� Routing message from {chat_id}: {text[:50]}...")

        # ၂။ AI Decision (Intent Detection)
        # လက်ရှိ modules folder ထဲမှာ ရှိတဲ့ module list ကို ယူမယ်
        available_modules = ["auto_pickup", "check_order", "auditor", "support"]
        
        prompt = f"""
        Role: Central AI Router for a Logistics Bot.
        Task: Analyze the user message and decide which module should handle it.
        
        Available Modules:
        - auto_pickup: Use for NEW pickup requests OR inquiries about pickup availability (e.g., "pick up လာယူပေးပါ", "ဒီနေ့ pickup ရဦးမလား", "မနက်ဖြန် pick up ရှိပါတယ်").
        - check_order: Use for checking order status, tracking numbers, or finding specific orders.
        - auditor: Use for complaints or when the user is asking about an ALREADY PLACED pickup (e.g., "pick up မလာသေးဘူးလား", "ဘယ်အချိန်လာမှာလဲ").
        - support: Use if the user is asking a general question about CarryMan, office location, or logistics services.
        - none: Use if the message is just a greeting, spam, or irrelevant.

        User Message: "{text}"

        Output Rules:
        1. Output ONLY the module name in lowercase.
        2. If unsure, output 'auditor'.
        3. If irrelevant, output 'none'.
        """

        intent = ai_utils.get_ai_completion(prompt, timeout=30.0)
        if not intent:
            log.error("❌ Both OpenRouter and Gemini Fallback failed.")
            return
        intent = intent.strip().lower()
        log.info(f"🎯 AI Decision: {intent}")

        if intent == "none":
            return

        # ၃။ Gatekeeper Logic (Phase 2)
        # Auto Pickup: ၂၄ နာရီ (Global ON ဖြစ်ရမည်)
        # AI Answer (Support/Auditor): 09:00 AM - 08:00 PM (Global & Group ON ဖြစ်ရမည်)
        
        # --- Private Chat Audit ---
        is_private = chat_id > 0

        if intent == "auto_pickup":
            if is_private:
                log.info(f"⏭️ Skipping Auto Pickup: Private Chat detected")
                return
            if not is_sandbox and global_pickup != 'ON':
                log.info(f"⏭️ Skipping Auto Pickup: Global Status is {global_pickup}")
                return
        elif intent == "auditor":
            if is_private:
                log.info(f"⏭️ Skipping Auditor: Private Chat detected")
                return
            if not is_sandbox:
                if global_ai != 'ON' or group_ai != 'ON' or not is_ai_office_hours():
                    log.info("⏭️ Skipping Auditor: Restrictions applied")
                    return
        else:
            # Support (AI Answer) အတွက် စစ်ဆေးခြင်း
            if not is_sandbox:
                if global_ai != 'ON':
                    log.info(f"⏭️ Skipping AI Answer: Global Status is {global_ai}")
                    return
                # Private chat doesn't have group_ai setting, so we skip that check for private
                if not is_private and group_ai != 'ON':
                    log.info(f"⏭️ Skipping AI Answer: Group Status is {group_ai}")
                    return
                if not is_ai_office_hours():
                    log.info("🌙 Skipping AI Answer: Outside Office Hours (09:00 AM - 08:00 PM)")
                    return

        # ၄။ Dynamic Loader (importlib)
        if intent == 'support':
            handle_ai_query(bot, message, is_automatic=True)
            return

        if intent in available_modules:
            try:
                # modules.intent ပုံစံဖြင့် import လုပ်မည်
                module_path = f"modules.{intent}"
                module = importlib.import_module(module_path)
                
                # Module တိုင်းမှာ handle(bot, message) function ရှိရမည်
                if hasattr(module, 'handle'):
                    module.handle(bot, message)
                else:
                    log.warning(f"⚠️ Module {intent} has no 'handle' function.")
            except ImportError as ie:
                log.error(f"❌ Could not import module {intent}: {ie}")
            except Exception as me:
                log.error(f"❌ Error executing module {intent}: {me}")
        else:
            log.warning(f"⚠️ AI suggested unknown module: {intent}")

    except Exception as e:
        log.error(f"❌ Router Error: {e}")
