# Version: 6.0 (OpenRouter - Gemini 3.1 Flash Lite Edition)
import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from logger import log

# Environment Variables Load လုပ်ခြင်း
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, '.env'))
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

def analyze_context(group_name, pending_msgs, resolved_msgs):
    """
    OpenRouter (google/gemini-3.1-flash-lite) ကိုသုံး၍ SLA စာများကို ခွဲခြမ်းစိတ်ဖြာပေးသော ဦးနှောက်
    """
    if not OPENROUTER_API_KEY:
        log.error("❌ OPENROUTER_API_KEY မရှိပါ။ .env ဖိုင်တွင် သေချာစစ်ဆေးပါ!")
        return None

    try:
        # OpenRouter Client တည်ဆောက်ခြင်း
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1", 
            api_key=OPENROUTER_API_KEY
        )

        p_text = "\n".join([f"ID: {m[0]} | Text: {m[1]}" for m in pending_msgs])
        r_text = "\n".join(resolved_msgs) if resolved_msgs else "None"

        system_prompt = f"""
        Role: Professional Delivery Service AI Manager for "{group_name}".
        Expertise: Multilingual (Burmese/English) context analysis and reasoning.
        
        Task: Analyze pending customer messages and group them into logical issues.
        
        Rules:
        1. ENTITY SEPARATION: Separate distinct customer requests into individual tickets.
        2. NATURAL BURMESE: Use natural, professional Burmese for issue summaries (3-5 words). 
           Avoid generic terms like "First item" or "Message 1".
           Examples: "ငွေလွှဲအတည်ပြုရန်", "ပစ္စည်းရောက်မရောက်စစ်ဆေးရန်", "လိပ်စာပြင်ဆင်ရန်".
        3. FILTERING: Ignore follow-up messages that don't require action (e.g., "K", "Thanks", "Ok").
        4. REASONING: Use the provided 'Resolved Context' to understand if a pending message is a new issue or a continuation.
        """

        user_prompt = f"""
        [Resolved Context]: 
        {r_text}

        [Pending Messages]: 
        {p_text}

        Output ONLY valid JSON in this format:
        {{
            "issues": [
                {{
                    "issue": "မြန်မာလိုအနှစ်ချုပ်",
                    "msg_ids": [array of IDs]
                }}
            ]
        }}
        If no actionable issues are found, return: {{"issues": []}}
        """

        # ⏳ Timeout 20 စက္ကန့်ဖြင့် ခေါ်ယူခြင်း
        response = client.chat.completions.create(
            model="google/gemini-3.1-flash-lite",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            timeout=20.0 
        )
        
        res_text = response.choices[0].message.content.strip()
        
        # JSON Validation
        data = json.loads(res_text)
        if not data.get("issues") and not isinstance(data.get("issues"), list):
            return None
            
        return res_text

    except Exception as e:
        log.error(f"❌ AI Engine Error v6.0 (Gemini): {e}")
        return None
