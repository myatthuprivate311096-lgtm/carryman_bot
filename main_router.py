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

def handle_ai_query(bot, message):
    """
    Smart AI Support Logic (DB -> Maps -> AI)
    """
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        # ၁။ User Level သတ်မှတ်ခြင်း
        user_level = db_manager.get_user_level(user_id, chat_id)
        
        # ၂။ မေးခွန်းကို ရယူခြင်း
        query = message.text.replace('/ai', '').strip()
        if not query and message.reply_to_message:
            query = message.reply_to_message.text or message.reply_to_message.caption
            
        if not query:
            bot.reply_to(message, "💡 **ဘယ်လိုကူညီပေးရမလဲခင်ဗျာ?**\n\nမေးခွန်းကို တွဲရိုက်ပါ (သို့) မေးလိုသောစာကို Reply ပြန်ပြီး `/ai` လို့ ရိုက်ပါ။")
            return

        log.info(f"🤖 AI Query from {user_id}: {query[:50]}...")

        # ၃။ Step 1: Database (Knowledge Base) Search
        kb_result = db_manager.search_knowledge(query, user_level)
        if kb_result:
            category, question, answer = kb_result
            response_text = f"🤖 **CarryMan AI Support (KB)**\n\n📌 **ကဏ္ဍ:** {category}\n❓ **မေးခွန်း:** {question}\n\n💡 **အဖြေ:** \n{answer}"
            bot.reply_to(message, response_text)
            return

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

        # ၅။ Step 3: General AI Response
        ai_prompt = f"""
        Role: Helpful Customer Support for CarryMan Logistics.
        User Query: "{query}"
        Language: Myanmar (Burmese)
        Tone: Professional and Friendly.
        
        Instructions:
        - Answer the user query based on general logistics knowledge.
        - If you don't know, suggest contacting a manager.
        - Keep it concise.
        """
        
        answer = ai_utils.get_ai_completion(ai_prompt, timeout=30.0)
        if not answer:
            bot.reply_to(message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")
            return
        bot.reply_to(message, f"🤖 **CarryMan AI Agent**\n\n{answer}")

    except Exception as e:
        log.error(f"❌ AI Query Error: {e}")
        bot.reply_to(message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")

def route_message(bot, message):
    """
    AI မှ Message ကို ဖတ်ပြီး သက်ဆိုင်ရာ Module ဆီသို့ လမ်းကြောင်းပြောင်းပေးခြင်း
    """
    try:
        chat_id = message.chat.id
        text = message.text or message.caption
        
        if not text:
            return

        # ၁။ Environment Mode စစ်ဆေးခြင်း
        env_mode = db_manager.get_setting('env_mode', 'Sandbox')
        
        if env_mode == 'Sandbox':
            # Sandbox Mode ဖြစ်ပါက Whitelist စစ်မည်
            if chat_id != SANDBOX_CHAT_ID:
                # log.info(f"ℹ️ Message from {chat_id} ignored (Sandbox Mode Active)")
                return
        # Production Mode ဖြစ်ပါက Whitelist မစစ်ဘဲ အကုန်ပေးဝင်မည်

        log.info(f"🧠 Routing message from {chat_id}: {text[:50]}...")

        # ၂။ AI Decision (Intent Detection)
        # လက်ရှိ modules folder ထဲမှာ ရှိတဲ့ module list ကို ယူမယ်
        available_modules = ["auto_pickup", "check_order", "auditor"]
        
        prompt = f"""
        Role: Central AI Router for a Logistics Bot.
        Task: Analyze the user message and decide which module should handle it.
        
        Available Modules:
        - auto_pickup: Use for NEW pickup requests OR inquiries about pickup availability (e.g., "pick up လာယူပေးပါ", "ဒီနေ့ pickup ရဦးမလား", "မနက်ဖြန် pick up ရှိပါတယ်").
        - check_order: Use for checking order status, tracking numbers, or finding specific orders.
        - auditor: Use for complaints or when the user is asking about an ALREADY PLACED pickup (e.g., "pick up မလာသေးဘူးလား", "ဘယ်အချိန်လာမှာလဲ").
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

        # ၃။ Dynamic Loader (importlib)
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
