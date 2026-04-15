# Version: 5.3 (OpenRouter - $5 Credit Utilization Edition)
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
    OpenRouter (GPT-4o-mini) ကိုသုံး၍ SLA စာများကို ခွဲခြမ်းစိတ်ဖြာပေးသော ဦးနှောက်
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

        prompt = f"""
        Role: Professional Delivery Service AI Manager. 
        Shop Name: "{group_name}"

        [Resolved Context - ဖြေရှင်းပြီးသားစာများ]: 
        {r_text}

        [Pending Messages - ဝန်ထမ်းဖြေရန်ကျန်သောစာများ]: 
        {p_text}

        ---
        Task & Rules:
        1. ENTITY SEPARATION: Customer မှ သီးခြားကိစ္စရပ်များ တောင်းဆိုလာပါက သီးခြား Ticket (Issue) များအဖြစ် ခွဲထုတ်ပါ။
        2. ISSUE NAMING (Burmese): အနှစ်ချုပ်ကို (၃-၅) လုံးဖြင့် သဘာဝကျသော မြန်မာစကားဖြင့် ရေးပါ။ "ပထမအထုပ်" စသည့် generic စကားများ ရှောင်ပါ။
        3. FILTERING: ဖြေပြီးသားကိစ္စ၏ အဆက် (ဥပမာ- ဟုတ်ကဲ့၊ ကျေးဇူး) များဖြစ်ပါက လျစ်လျူရှုပါ။

        Output ONLY valid JSON:
        {{
            "issues": [
                {{
                    "issue": "မြန်မာလိုအနှစ်ချုပ်",
                    "msg_ids": [array of IDs]
                }}
            ]
        }}
        ဖြေစရာမရှိပါက "issues": [] ဟုသာ ပြန်ပေးပါ။
        """

        # ⏳ Timeout 20 စက္ကန့်ဖြင့် ခေါ်ယူခြင်း
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=20.0 
        )
        
        res_text = response.choices[0].message.content.strip()
        
        # JSON Validation
        data = json.loads(res_text)
        if not data.get("issues"):
            return None
            
        return res_text

    except Exception as e:
        log.error(f"❌ AI Engine Error v5.3: {e}")
        return None