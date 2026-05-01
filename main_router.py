# Version: 1.1 (Manual AI Trigger Update)
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
    """
    AI Auto-Answer အလုပ်လုပ်မည့်အချိန် (၂၄ နာရီ ဖွင့်ထားသည်)
    အစ်ကို့တောင်းဆိုချက်အရ Private Chat AI Reply အတွက် ၂၄ နာရီ ဖွင့်ပေးထားခြင်း ဖြစ်ပါသည်။
    """
    return True

def handle_ai_query(bot, message, is_automatic=False):
    """
    Smart AI Support Logic (DB -> Maps -> AI)
    """
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        is_private = chat_id > 0
        is_sandbox = (chat_id == SANDBOX_CHAT_ID)

        # 🛑 Group Chat Restriction: AI Auto-Answer is DISABLED in Groups for automatic replies.
        # Manual /ai queries are allowed.
        if not is_private and not is_sandbox and is_automatic:
            log.info(f"🔇 AI Auto-Answer is disabled in Group Chat {chat_id} for automatic replies. Returning silently.")
            return

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

        # ၃။ Step 1: AI Response with Tool Access Control
        is_staff = user_level >= 3
        
        scope_check_prompt = ""
        if is_private and not is_staff:
            scope_check_prompt = """
            Scope Check: 'Determine if the user query is related to CarryMan Logistics services (delivery, tracking, pickup, pricing, locations).
            If it is OUT OF SCOPE (e.g., coding, math, general knowledge, personal questions), output ONLY the word "OUT_OF_SCOPE".
            Otherwise, proceed with the answer.'
            """

        rag_instructions = ai_utils.get_rag_instructions(user_level)

        # Permanent Base Context (Company Info)
        base_company_info = """
        [Base Company Info]:
        - Office Address: အမှတ်(၁)၊ ဇေယျသုခလမ်း၊ နှင်းဆီကုန်းဘူတာအနီး၊ သင်္ဃန်းကျွန်းမြို့နယ်၊ ရန်ကုန်မြို့။
        - Office Hours: နေ့စဉ် မနက် ၉ နာရီမှ ညနေ ၆ နာရီအထိ (အခါကြီးရက်ကြီးများသာ ပိတ်ပါသည်)။
        - Contact Numbers: 09789102234, 09899065899
        - Google Maps: https://maps.app.goo.gl/CarryManRealLocation (အစ်ကို့ရဲ့ တကယ့် Link ကို ဒီမှာ အစားထိုးနိုင်ပါတယ်)
        """

        # Fetch Core Policies Dynamically from Database
        core_policies = db_manager.get_core_policies()

        ai_prompt = f"""
        Strict Persona & Tone: 'You are an Online Shop (OS) admin. You MUST strictly follow the tone, style, and examples provided in the OS Tone_&_Example data. Keep answers short, direct, and natural. NEVER use generic AI fluff.'

        {rag_instructions}

        {scope_check_prompt}

        {base_company_info}

        {core_policies}

        RULE: You must apply LOGICAL REASONING using the Base Context (including the Dynamic Core Policies) and retrieved data.
        If a user asks about an item (e.g., plates, guns, glass, liquid), evaluate it against the provided Terms and Conditions instead of looking for exact word matches.
        - Example: If asked about 'plates' (ပန်းကန်), reason that it is a 'Fragile Item' and apply the fragile item rule found in the policies.
        - Example: If asked about 'Kyaikto' (ကျိုက်ထို), use the 'search_database' tool to find the specific rules for that location.

        CRITICAL: You MUST use the 'search_database' tool to look up specific details (locations, pricing) BEFORE deciding you don't know the answer.

        Comprehensive Data Extraction: 'When a user asks about delivery to a specific location, you MUST use the search_database tool and extract:
        - Whether Home Delivery is available.
        - The Delivery Fee (Base weight and extra charge).
        - Whether COD (Cash on Delivery) is accepted.
        - Estimated Delivery Duration (Days).'

        Location Labeling: 'Always clearly state the Township and City in your response.'

        Format Constraint: 'Combine these details into a single, concise, human-like paragraph in Burmese.'

        User Query: "{query}"
        """
        
        # Get tools based on user level (Binary Access Control)
        tools = ai_utils.get_ai_tools(user_level)
        
        # Initial AI Call
        response = ai_utils.get_ai_completion(ai_prompt, timeout=30.0, tools=tools, user_level=user_level)
        
        if not response:
            bot.reply_to(message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")
            return

        # Handle Tool Calls (if any)
        # Note: OpenRouter/OpenAI response might contain tool_calls
        # For simplicity in this implementation, we'll handle a single turn of tool calling
        # In a production environment, you might want a loop for multiple tool calls.
        
        # We need to check if the response is a tool call or a direct answer.
        # Since get_ai_completion returns content string, we might need to adjust it
        # to return the full response object if we want to handle tool calls properly.
        # However, for now, let's assume get_ai_completion handles the tool execution
        # or we modify it to handle the logic.
        
        # Let's refine get_ai_completion in ai_utils.py to handle tool execution internally
        # to keep main_router clean.
        answer = response

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
                
                from telebot import types
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔓 Unmute AI", callback_data=f"unmute_user:{user_id}"))

                try:
                    bot.send_message(admin_chat, alert_text, message_thread_id=topic_id, reply_markup=markup)
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
        
        # Split message if too long (Telegram limit 4096)
        full_response = f"🤖 **CarryMan AI Agent**\n\n{answer}"
        if len(full_response) > 4000:
            parts = [full_response[i:i+4000] for i in range(0, len(full_response), 4000)]
            for idx, part in enumerate(parts):
                if idx == 0:
                    bot.reply_to(message, part)
                else:
                    bot.send_message(message.chat.id, part)
        else:
            bot.reply_to(message, full_response)
            
        # Mark as Handled by AI to suppress escalations
        db_manager.update_message_status(message.message_id, chat_id, 'HANDLED_BY_AI')

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
        
        # 🛑 Strict Staff Exclusion: Group Chat တွင် ဝန်ထမ်းဖြစ်ပါက AI ဆီသို့ လုံးဝမပို့ဘဲ ချက်ချင်းရပ်မည်
        # Private Chat တွင်မူ Staff များ AI မေးခွန်းမေးမြန်းနိုင်ရန် ခွင့်ပြုမည် (Rule #2)
        user_level = db_manager.get_user_level(user_id, chat_id)
        is_staff = user_level >= 3
        is_private = chat_id > 0

        if not is_private and is_staff:
            log.info(f"🛡️ Staff Exclusion (Group): Skipping AI routing for staff {user_id}")
            return

        text = message.text or message.caption
        
        if not text:
            return

        # ၁။ Global & Group Status စစ်ဆေးခြင်း (Phase 2)
        global_ai = db_manager.get_ai_global_status()
        
        # 💡 Manual AI Trigger Check (/ai)
        # အစ်ကို့တောင်းဆိုချက်အရ /ai ပါမှသာ AI Answer အလုပ်လုပ်ပါမည်။
        is_manual_ai = text.strip().startswith('/ai')
        
        if is_manual_ai:
            is_sandbox = (chat_id == SANDBOX_CHAT_ID)
            if global_ai == 'ON' or is_sandbox:
                handle_ai_query(bot, message, is_automatic=False)
            else:
                log.info(f"🔇 AI Command (/ai) ignored because global_ai_answer is {global_ai}")
            return
        group_ai = db_manager.get_group_ai_status(chat_id)
        global_pickup = db_manager.get_auto_pickup_global_status()
        global_alert = db_manager.get_alert_system_global_status()
        
        is_sandbox = (chat_id == SANDBOX_CHAT_ID)
        
        if is_sandbox:
            log.info(f"🧪 Sandbox Mode: Bypassing all global/group/time restrictions for chat {chat_id}")
            # Force all toggles to ON for sandbox
            global_ai = 'ON'
            group_ai = 'ON'
            global_pickup = 'ON'
        
        log.info(f"Routing message from {chat_id}: {text[:50]}...")

        # ၂။ AI Decision (Intent Detection)
        # လက်ရှိ modules folder ထဲမှာ ရှိတဲ့ module list ကို ယူမယ်
        # 💡 support module ကို automatic routing မှ ဖယ်ထုတ်ထားပါသည် (Manual /ai သာ သုံးမည်)
        available_modules = ["auto_pickup", "check_order", "auditor"]
        
        prompt = f"""
        Role: Central AI Router for a Logistics Bot.
        Task: Analyze the user message and decide which module should handle it.
        
        Available Modules:
        - auto_pickup: Use for NEW pickup requests OR inquiries about pickup availability (e.g., "pick up လာယူပေးပါ", "လာကောက်ပေးပါ", "မနက်ဖြန်အတွက် တင်ပေးပါ", "pick up ရဦးမလား", "ဒီနေ့ pickup ရှိလား").
          CRITICAL: If the message is just sharing a list (e.g., "စာရင်းလေးပါ", "pickup စာရင်းလေးပါ"), discussing a past order, or mentioning "pickup" without requesting a new one or inquiring about availability, output 'none'.
        - check_order: Use for checking order status, tracking numbers, or finding specific orders.
        - auditor: Use for complaints or when the user is asking about an ALREADY PLACED pickup (e.g., "pick up မလာသေးဘူးလား", "ဘယ်အချိန်လာမှာလဲ").
        - none: Use if the message is just a greeting, spam, sharing a list, general question, or irrelevant.

        User Message: "{text}"

        Output Rules:
        1. Output ONLY the module name in lowercase.
        2. If the message is just sharing a list or info (e.g., "စာရင်းလေးပါ") without requesting a new pickup, output 'none'.
        3. If the message is a general question (e.g., office location, contact info) without /ai, output 'none'.
        4. If unsure, output 'auditor'.
        5. If irrelevant, output 'none'.
        """

        intent = ai_utils.get_ai_completion(prompt, timeout=30.0)
        if not intent:
            log.error("❌ Both OpenRouter and Gemini Fallback failed.")
            return
        intent = intent.strip().lower()
        log.info(f"🎯 AI Decision: {intent}")

        if intent == "none":
            return

        # 🛑 Group Chat Restriction: ONLY allow auto_pickup and auditor.
        # Block support, check_order, and any other general AI routing in Groups.
        if not is_private and not is_sandbox:
            allowed_group_intents = ["auto_pickup", "auditor"]
            if intent not in allowed_group_intents:
                log.info(f"🔇 Blocking general intent '{intent}' in Group Chat {chat_id}. Bot will remain silent.")
                return

        # ၃။ Gatekeeper Logic (Phase 2)
        # Auto Pickup: ၂၄ နာရီ (Global ON ဖြစ်ရမည်)
        # AI Answer (Support/Auditor): 09:00 AM - 08:00 PM (Global & Group ON ဖြစ်ရမည်)
        
        # --- Private Chat Audit ---
        is_private = chat_id > 0
        user_level = db_manager.get_user_level(user_id, chat_id)
        is_staff = user_level >= 3

        if intent == "auto_pickup":
            # Rule: Pickup works ONLY in Group Chats for Non-Staff users.
            if is_private:
                log.info(f"⏭️ Skipping Auto Pickup: Private Chat detected. Pickup is Group-only.")
                return
            if is_staff:
                log.info(f"🛡️ Staff Safety Net: Blocking auto_pickup routing for staff {user_id}")
                return
            # Note: Notification is sent regardless of global_pickup status.
            # Silent mode for groups (when OFF) is handled inside the module.
        elif intent == "auditor":
            if is_private and not is_staff:
                log.info(f"⏭️ Skipping Auditor: Private Chat detected for non-staff")
                return
            if not is_sandbox:
                if not is_private and global_alert != 'ON':
                    log.info("⏭️ Skipping Auditor: Alert System is OFF")
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
        if intent == 'auditor' and is_private and is_staff:
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
