import sys
import os
import json

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_utils
import db_manager

def test_reasoning(query, user_level=1):
    # Mocking the prompt construction from main_router.py
    rag_instructions = ai_utils.get_rag_instructions(user_level)
    
    base_company_info = """
    [Base Company Info]:
    - Office Address: အမှတ်(၁)၊ ဇေယျသုခလမ်း၊ နှင်းဆီကုန်းဘူတာအနီး၊ သင်္ဃန်းကျွန်းမြို့နယ်၊ ရန်ကုန်မြို့။
    - Office Hours: နေ့စဉ် မနက် ၉ နာရီမှ ညနေ ၆ နာရီအထိ (အခါကြီးရက်ကြီးများသာ ပိတ်ပါသည်)။
    - Contact Numbers: 09789102234, 09899065899
    - Google Maps: https://maps.app.goo.gl/CarryManRealLocation
    """

    # Fetch Core Policies Dynamically from Database
    core_policies = db_manager.get_core_policies()

    ai_prompt = f"""
    Strict Persona & Tone: 'You are an Online Shop (OS) admin. You MUST strictly follow the tone, style, and examples provided in the OS Tone_&_Example data. Keep answers short, direct, and natural. NEVER use generic AI fluff.'

    {rag_instructions}

    {base_company_info}

    {core_policies}

    RULE: You must apply LOGICAL REASONING using the Base Context (including the Dynamic Core Policies) and retrieved data. 
    If a user asks about an item (e.g., plates, guns, glass, liquid), evaluate it against the provided Terms and Conditions instead of looking for exact word matches.

    CRITICAL: You MUST use the 'search_database' tool to look up specific details (locations, pricing) BEFORE deciding you don't know the answer.

    User Query: "{query}"
    """
    
    tools = ai_utils.get_ai_tools(user_level)
    response = ai_utils.get_ai_completion(ai_prompt, timeout=30.0, tools=tools, user_level=user_level)
    return response

if __name__ == "__main__":
    test_queries = [
        "ပန်းကန်တွေ ပို့ပေးလား", # Plates (Fragile)
        "သေနတ် ပို့လို့ရမလား", # Gun (Prohibited)
        "ကျိုက်ထို ပို့ခ ဘယ်လောက်လဲ" # Location (Needs DB Search)
    ]

    for q in test_queries:
        print(f"\n❓ Query: {q}")
        ans = test_reasoning(q)
        print(f"🤖 AI: {ans}")
