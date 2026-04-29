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

# State tracking to avoid redundant notifications
# 0 = Primary (OpenRouter), 1 = Fallback (Gemini)
_current_ai_mode = 0
_last_openrouter_fail_time = 0
_openrouter_fail_count = 0
_last_critical_alert_time = 0
OPENROUTER_COOLDOWN = 900  # 15 minutes in seconds
MAX_OPENROUTER_FAILS = 3   # Number of consecutive fails before cooldown
CRITICAL_ALERT_COOLDOWN = 1800 # 30 minutes between critical alerts

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
        - ZERO HALLUCINATION POLICY: You are strictly a database reader.
        CRITICAL RULE: You are strictly forbidden from using your pre-trained knowledge. You MUST NOT make up or guess any addresses, phone numbers, delivery fees, or links. You must answer ONLY using the exact information found in the provided Context. If the Context does not contain the explicit answer to the user's query, you MUST reply EXACTLY with: 'တောင်းပန်ပါတယ်ခင်ဗျာ။ ဒီအချက်အလက်ကို ကျွန်တော် အတိအကျ မသိသေးပါဘူး။ အသေးစိတ်သိရှိလိုပါက Customer Service ကို ဆက်သွယ်မေးမြန်းနိုင်ပါတယ်ခင်ဗျာ။' and nothing else.

        STRICT FORMATTING RULES (LEVEL 1):
        - Pricing: You MUST use exact base weight and extra charge format (e.g., "1kg ထိ 2500 ကျပ်၊ အပို 1kg လျှင် 500 ကျပ်").
        - NO RANGES: Never provide price ranges (e.g., "2000-3000 ကျပ်"). Use exact numbers from the context.
        - Natural Tone: Keep the response short and natural in Burmese, but strictly factual.
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

def call_gemini_direct(prompt, model="gemini-2.0-flash", response_format=None):
    """Direct Gemini API call (Cheap & Fast)"""
    # Use v1beta for newer models like gemini-2.0-flash
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={FALLBACK_GEMINI_API_KEY}"
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

def call_groq_direct(prompt, model="llama-3.3-70b-versatile", response_format=None):
    """Direct Groq API call (Ultra Fast Fallback)"""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
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

def get_ai_completion(prompt, model="google/gemini-3.1-flash-lite-preview", response_format=None, timeout=30.0, tools=None, tool_choice=None):
    """
    Centralized AI call with Auto-Recovery and Manager Notifications.
    Always tries OpenRouter first.
    """
    global _current_ai_mode, _last_openrouter_fail_time, _openrouter_fail_count, _last_critical_alert_time
    
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
                "model": model,
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
                    # Get user_level from prompt context if possible, or pass it as an argument
                    # For now, we'll assume user_level is handled by the caller or extracted
                    # But since this is a centralized function, let's try to find user_level in prompt
                    user_level = 1
                    if "[USER_LEVEL: STAFF/MANAGER]" in prompt:
                        user_level = 3
                    
                    tool_result = execute_tool_call(tool_call, user_level)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": tool_result
                    })
                
                # Second call to get the final answer
                final_response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    timeout=timeout
                )
                content = final_response.choices[0].message.content
            
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

    # 2. Try Fallback (Gemini Direct)
    if FALLBACK_GEMINI_API_KEY:
        # Use gemini-2.0-flash as it's the cheapest and very capable
        fallback_content = call_gemini_direct(prompt, model="gemini-2.0-flash", response_format=response_format)
        if fallback_content:
            return fallback_content

    # 3. Try Tertiary Fallback (Groq)
    if GROQ_API_KEY:
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
    Level 1: Only Database Retrieval.
    Level 3+: Database + OSM.
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
                        "query": {
                            "type": "string",
                            "description": "The search query (e.g., 'မြစ်ကြီးနား deli ခ')",
                        }
                    },
                    "required": ["query"],
                },
            },
        }
    ]

    if user_level >= 3:
        tools.append({
            "type": "function",
            "function": {
                "name": "search_location",
                "description": "Search for a location's township and city using OpenStreetMap (OSM). Use this for address verification or finding locations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The location name to search for.",
                        }
                    },
                    "required": ["query"],
                },
            },
        })
    
    return tools

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
            category, question, answer = result
            return f"Category: {category}\nQuestion: {question}\nAnswer: {answer}"
        return "No information found in the database for this query."
    
    elif name == "search_location" and user_level >= 3:
        query = args.get("query")
        log.info(f"🛠️ Tool Call: search_location('{query}')")
        township, source = location_service.get_location_with_fallback(query)
        if township:
            return f"Location Found: {township} (Source: {source})"
        return "Location not found."
    
    return "Error: Unauthorized tool or unknown function."

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

def handle_tool_calls(tool_calls, user_level):
    """Executes tool calls and returns results for the AI"""
    import db_manager
    from modules import location_service
    
    results = []
    for tool_call in tool_calls:
        func_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        
        log.info(f"🛠 AI calling tool: {func_name} with args: {args}")
        
        result_content = ""
        if func_name == "search_database":
            # Enforce user_level restriction at the tool level
            kb_result = db_manager.search_knowledge(args.get("query"), user_level)
            if kb_result:
                category, question, answer = kb_result
                result_content = f"Category: {category}\nQuestion: {question}\nAnswer: {answer}"
            else:
                result_content = "No matching information found in the database."
        
        elif func_name == "search_location" and user_level >= 3:
            loc_str, source = location_service.get_location_with_fallback(args.get("query"))
            result_content = f"Location: {loc_str}\nSource: {source}" if loc_str else "Location not found."
        
        results.append({
            "tool_call_id": tool_call.id,
            "role": "tool",
            "name": func_name,
            "content": result_content
        })
    
    return results
