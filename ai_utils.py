import os
import json
import requests
import time
from openai import OpenAI
from logger import log
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
FALLBACK_GEMINI_API_KEY = os.getenv('FALLBACK_GEMINI_API_KEY')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
MANAGER_ID = os.getenv('MANAGER_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
PRIMARY_AI_PROVIDER = os.getenv('PRIMARY_AI_PROVIDER', 'openrouter').strip().lower()
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'deepseek/deepseek-v4-flash')
FALLBACK_GEMINI_MODEL = os.getenv('FALLBACK_GEMINI_MODEL', 'gemini-1.5-flash')
FALLBACK_GROQ_MODEL = os.getenv('FALLBACK_GROQ_MODEL', 'llama-3.3-70b-versatile')

# State tracking to avoid redundant notifications
# 0 = Primary (OpenRouter), 1 = Fallback (Gemini)
_current_ai_mode = 0
_last_openrouter_fail_time = 0
_openrouter_fail_count = 0
_last_critical_alert_time = 0
OPENROUTER_COOLDOWN = 900  # 15 minutes in seconds
MAX_OPENROUTER_FAILS = 3   # Number of consecutive fails before cooldown
CRITICAL_ALERT_COOLDOWN = 1800 # 30 minutes between critical alerts

def is_groq_key_configured():
    """Skip Groq fallback when .env still has a placeholder or empty key."""
    key = (GROQ_API_KEY or "").strip()
    if not key:
        return False
    upper = key.upper()
    if "REPLACE_WITH" in upper or upper in {"YOUR_GROQ_API_KEY", "CHANGEME"}:
        return False
    return key.startswith("gsk_") and len(key) > 20

def clean_json_response(text):
    """
    Cleans AI response to extract only the JSON part.
    Handles markdown code blocks and unescaped newlines.
    """
    if not text:
        return None
    
    text = text.strip()
    
    # Remove Markdown code blocks if present
    if text.startswith("```"):
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
    
    # Basic cleanup for common AI JSON mistakes
    # Replace literal newlines within strings (this is tricky but helps)
    # Note: This is a simple heuristic.
    return text

def clean_ai_json(text):
    """
    Cleans AI response to extract valid JSON.
    Removes markdown wrappers and handles common formatting issues.
    """
    if not text:
        return None
    
    text = text.strip()
    
    # Remove Markdown code blocks if present
    if text.startswith("```"):
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
    
    # Handle unescaped newlines within JSON strings (common AI mistake)
    # We look for newlines that occur inside double quotes
    import re
    
    # This regex tries to find newlines that are inside quotes.
    # It's not perfect for all cases but covers the most common "unterminated string" issue.
    def replace_newlines(match):
        return match.group(0).replace('\n', '\\n').replace('\r', '\\r')
    
    # Match content between double quotes, including newlines
    text = re.sub(r'"[^"]*?"', replace_newlines, text, flags=re.DOTALL)
    
    return text

def get_rag_instructions(user_level):
    """
    Returns strict RAG instructions based on user level.
    Level 3 & 4: Staff/Manager (Full Access)
    Level 1 & 2: Customer/OS (Strict RAG)
    """
    if user_level >= 3:
        return """
        [USER_LEVEL: STAFF/MANAGER]
        - You have FULL access to all database tables and Map tools.
        - Assist the user fully and analyze data for them.
        - Use your general knowledge if needed to provide helpful context, but prioritize database facts.
        """
    else:
        return """
        [USER_LEVEL: LEVEL 1 DATABASE READER]
        - REASONING-BASED RAG POLICY: You are an evaluator and a database reader.
        CRITICAL RULE: You must apply logical reasoning using the Base Context and retrieved data. You are strictly forbidden from using your pre-trained knowledge for specific facts (addresses, phone numbers, delivery fees). However, you MUST use reasoning to evaluate user items against the provided Terms and Conditions.
        If the Context (Base Context + Database) does not contain enough information to reason out an answer, you MUST reply EXACTLY with: 'တောင်းပန်ပါတယ်ခင်ဗျာ။ ဒီအချက်အလက်ကို ကျွန်တော် အတိအကျ မသိသေးပါဘူး။ အသေးစိတ်သိရှိလိုပါက Customer Service ကို ဆက်သွယ်မေးမြန်းနိုင်ပါတယ်ခင်ဗျာ။' and nothing else.

        STRICT FORMATTING RULES (LEVEL 1):
        - Pricing: You MUST use exact base weight and extra charge format (e.g., "1kg ထိ 2500 ကျပ်၊ အပို 1kg လျှင် 500 ကျပ်").
        - NO RANGES: Never provide price ranges (e.g., "2000-3000 ကျပ်"). Use exact numbers from the context.
        - Natural Tone: Keep the response short and natural in Burmese, but strictly factual.
        """

def get_topic_ai_instructions(topic):
    """Topic-scoped system instructions for manual /ai (delivery vs status)."""
    if topic == "status_arrival":
        return """
        [TOPIC: STATUS_ARRIVAL]
        Live tracking is handled outside this prompt. If you still receive this topic tag, reply briefly that tracking is checked via the website.
        """
    return """
        [TOPIC: DELIVERY_INFO]
        - Use ONLY Retrieved Knowledge Base Data (Google Sheet sync) and Core Policies.
        - NEVER invent delivery fees, townships, COD rules, or delivery days.
        - If data is insufficient, say you do not know exactly — do not guess.
        - Match OS Tone & Example snippets: short, direct, natural Burmese (OS admin voice).
        - One concise paragraph unless user asked multiple distinct questions.
        """

def send_manager_notification(text):
    """Sends a notification to the manager via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not MANAGER_ID:
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MANAGER_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error(f"❌ Failed to send manager notification: {e}")

def send_manager_photo(photo_path, caption):
    """Sends a photo to the manager via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not MANAGER_ID or not os.path.exists(photo_path):
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {
                "chat_id": MANAGER_ID,
                "caption": caption,
                "parse_mode": "HTML"
            }
            requests.post(url, files=files, data=data, timeout=30)
    except Exception as e:
        log.error(f"❌ Failed to send manager photo: {e}")

def call_gemini_direct(prompt, model=None, response_format=None):
    """Direct Gemini API call (Cheap & Fast)"""
    selected_model = model or FALLBACK_GEMINI_MODEL
    # Use v1beta for newer models
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent?key={FALLBACK_GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    # Handle response format if needed (Gemini 1.5 supports JSON mode)
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    if response_format and response_format.get("type") == "json_object":
        data["generationConfig"] = {"response_mime_type": "application/json"}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            log.error(f"❌ Gemini Direct Error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        log.error(f"❌ Gemini Direct Exception: {e}")
        return None

def call_groq_direct(prompt, model=None, response_format=None):
    """Direct Groq API call (Ultra Fast Fallback)"""
    if not is_groq_key_configured():
        return None

    selected_model = model or FALLBACK_GROQ_MODEL
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": selected_model,
        "messages": [{"role": "user", "content": prompt}]
    }
    
    if response_format and response_format.get("type") == "json_object":
        data["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content'].strip()
        else:
            log.error(f"❌ Groq Direct Error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        log.error(f"❌ Groq Direct Exception: {e}")
        return None

def get_ai_completion(prompt, model=None, response_format=None, timeout=30.0, tools=None, tool_choice=None, user_level=1):
    """
    Centralized AI call with Auto-Recovery and Manager Notifications.
    Primary provider and models are configured from .env.
    """
    global _current_ai_mode, _last_openrouter_fail_time, _openrouter_fail_count, _last_critical_alert_time
    
    selected_provider = PRIMARY_AI_PROVIDER if PRIMARY_AI_PROVIDER in {"openrouter", "gemini", "groq"} else "openrouter"
    if selected_provider != PRIMARY_AI_PROVIDER:
        log.warning(f"⚠️ Invalid PRIMARY_AI_PROVIDER '{PRIMARY_AI_PROVIDER}'. Falling back to 'openrouter'.")

    openrouter_model = model or OPENROUTER_MODEL

    # Respect PRIMARY_AI_PROVIDER from .env before using default failover chain.
    if selected_provider == "gemini" and FALLBACK_GEMINI_API_KEY:
        primary_content = call_gemini_direct(prompt, response_format=response_format)
        if primary_content:
            return primary_content
    elif selected_provider == "groq" and is_groq_key_configured():
        primary_content = call_groq_direct(prompt, response_format=response_format)
        if primary_content:
            return primary_content

    # 1. Try Primary (OpenRouter)
    can_try_openrouter = False
    if OPENROUTER_API_KEY:
        if _current_ai_mode == 0:
            can_try_openrouter = True
        else:
            # If in fallback mode, check if cooldown has passed
            elapsed = time.time() - _last_openrouter_fail_time
            if elapsed >= OPENROUTER_COOLDOWN:
                log.info(f"🔄 Cooldown finished ({int(elapsed)}s). Retrying OpenRouter...")
                can_try_openrouter = True
            else:
                remaining = int(OPENROUTER_COOLDOWN - elapsed)
                log.info(f"⏳ OpenRouter is in cooldown. {remaining}s remaining. Using Gemini.")

    if can_try_openrouter:
        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=OPENROUTER_API_KEY
            )
            
            # Prepare arguments for OpenAI client
            kwargs = {
                "model": openrouter_model,
                "messages": [{"role": "user", "content": prompt}],
                "timeout": timeout
            }
            if response_format:
                kwargs["response_format"] = response_format
            if tools:
                kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

            response = client.chat.completions.create(**kwargs)
            message_obj = response.choices[0].message
            content = message_obj.content
            
            # Handle Tool Calls
            if message_obj.tool_calls:
                log.info(f"🔧 AI requested {len(message_obj.tool_calls)} tool calls.")
                messages = [{"role": "user", "content": prompt}, message_obj]
                
                for tool_call in message_obj.tool_calls:
                    tool_result = execute_tool_call(tool_call, user_level)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": tool_result
                    })
                
                # Second call to get the final answer
                final_response = client.chat.completions.create(
                    model=openrouter_model,
                    messages=messages,
                    timeout=timeout
                )
                content = final_response.choices[0].message.content
                if not content:
                    tool_contents = [
                        m.get("content") for m in messages
                        if isinstance(m, dict) and m.get("role") == "tool" and m.get("content")
                    ]
                    if tool_contents:
                        log.warning("⚠️ OpenRouter empty after tool call — using database search result directly.")
                        return tool_contents[0][:3500]
                    log.warning("⚠️ OpenRouter returned empty content after tool call.")
            
            # Success! Reset fail count
            _openrouter_fail_count = 0
            
            # If we were in fallback mode, notify recovery
            if _current_ai_mode == 1:
                _current_ai_mode = 0
                log.info("✅ OpenRouter recovered. Switching back to Primary API.")
                send_manager_notification("✅ **AI System Recovery**\n\nOpenRouter API ပြန်လည်အဆင်ပြေသွားပါပြီ။ မူလ API ကို ပြန်လည်အသုံးပြုနေပါသည်။")
            
            return content.strip() if content else None
            
        except Exception as e:
            _openrouter_fail_count += 1
            log.warning(f"⚠️ OpenRouter Error ({_openrouter_fail_count}/{MAX_OPENROUTER_FAILS}): {e}")
            
            # If we reached max failures and were in primary mode, enter cooldown
            if _openrouter_fail_count >= MAX_OPENROUTER_FAILS and _current_ai_mode == 0:
                _current_ai_mode = 1
                _last_openrouter_fail_time = time.time()
                log.error(f"🚨 OpenRouter failed {MAX_OPENROUTER_FAILS} times. Entering 15-min cooldown.")
                send_manager_notification(
                    f"🚨 **AI System Alert**\n\nOpenRouter API {MAX_OPENROUTER_FAILS} ကြိမ်ဆက်တိုက် error တက်နေပါသည်။ "
                    f"Fallback API (Gemini 2.0 Flash) သို့ ၁၅ မိနစ်ခန့် ခေတ္တပြောင်းလဲအသုံးပြုနေပါသည်။\n\n"
                    f"Last Error: `{str(e)[:100]}`"
                )

    # 2. Try Gemini Direct fallback
    if selected_provider != "gemini" and FALLBACK_GEMINI_API_KEY:
        fallback_content = call_gemini_direct(prompt, response_format=response_format)
        if fallback_content:
            return fallback_content

    # 3. Try Groq fallback
    if selected_provider != "groq" and is_groq_key_configured():
        groq_content = call_groq_direct(prompt, response_format=response_format)
        if groq_content:
            log.info("⚡ Groq Fallback used successfully.")
            return groq_content

    log.error("❌ All AI systems (OpenRouter, Gemini, Groq) failed.")
    
    # Send Critical Alert to Manager (Max once every 30 minutes to avoid spam)
    current_time = time.time()
    if current_time - _last_critical_alert_time >= CRITICAL_ALERT_COOLDOWN:
        _last_critical_alert_time = current_time
        send_manager_notification(
            "🚨 **CRITICAL: AI SYSTEM TOTAL FAILURE**\n\n"
            "OpenRouter, Gemini နှင့် Groq API အားလုံး အလုပ်မလုပ်တော့ပါ။\n\n"
            "ဖြစ်နိုင်ခြေများ -\n"
            "၁။ API Keys များတွင် ပိုက်ဆံကုန်သွားခြင်း\n"
            "၂။ Rate Limit အပြင်းအထန် မိနေခြင်း\n"
            "၃။ Internet/Server ချိတ်ဆက်မှု ပြဿနာ\n\n"
            "ကျေးဇူးပြု၍ API Dashboard များကို စစ်ဆေးပေးပါ အစ်ကို။"
        )
        
    return None

def get_ai_tools(user_level):
    """
    Returns tool definitions based on user level.
    Level 1: Only Database Search
    Level 3+: Database Search + OSM Maps
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_database",
                "description": "Search the CarryMan knowledge base for delivery fees, office info, and general logistics questions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query in Burmese or English."}
                    },
                    "required": ["query"]
                }
            }
        }
    ]

    if user_level >= 3:
        tools.append({
            "type": "function",
            "function": {
                "name": "search_location",
                "description": "Search for a specific location or township using OSM Maps to find the correct City and Township name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The location name to search for."}
                    },
                    "required": ["query"]
                }
            }
        })
    
    return tools

def format_human_like_answer(answer, tone_block=None):
    """Light post-processing to keep /ai replies concise and natural."""
    if not answer:
        return answer

    text = str(answer).strip()
    fluff_markers = (
        "As an AI",
        "I'm an AI",
        "ကျွန်တော်က AI",
        "အကူအညီပေးရတာ ဝမ်းသာပါတယ်",
    )
    for marker in fluff_markers:
        if marker in text:
            text = text.replace(marker, "").strip()

    if tone_block and "short" in tone_block.lower():
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) > 6:
            text = "\n".join(lines[:6])

    return text

def execute_tool_call(tool_call, user_level):
    """Executes a single tool call and returns the result as a string."""
    import db_manager
    from modules import location_service
    
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    
    if name == "search_database":
        query = args.get("query")
        log.info(f"🛠️ Tool Call: search_database('{query}', level={user_level})")
        result = db_manager.search_knowledge(query, user_level)
        if result:
            # search_knowledge now returns a combined string of multiple results
            return result
        return "No information found in the database for this query."
    
    elif name == "search_location" and user_level >= 3:
        query = args.get("query")
        log.info(f"🛠️ Tool Call: search_location('{query}')")
        township, source = location_service.get_location_with_fallback(query)
        if township:
            return f"Location Found: {township} (Source: {source})"
        return "Location not found."
    
    return "Error: Unauthorized tool or unknown function."
