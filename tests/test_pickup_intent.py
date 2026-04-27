import sys
import os
import json

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_utils

def test_router_intent(text):
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
    return intent.strip().lower() if intent else None

def test_pickup_extraction(text):
    extract_prompt = f"""
    Analyze the following message for a NEW pickup request or an inquiry about pickup availability.
    Message: "{text}"

    Output ONLY a JSON object with:
    - is_new_request: boolean (True if this is a request to pick up items OR an inquiry if pickup is available, False if it's a question about status of an existing order, complaint, or something else)
    - vehicle: "Bicycle" or "Car" (Default to null if not mentioned)
    - date_type: "today" or "tomorrow" (Default to "tomorrow" if not mentioned)
    - remark: A short summary of the request in English.
    """
    ai_res_content = ai_utils.get_ai_completion(
        prompt=extract_prompt,
        model="google/gemini-2.0-flash-001",
        response_format={ "type": "json_object" },
        timeout=30.0
    )
    return json.loads(ai_res_content) if ai_res_content else None

if __name__ == "__main__":
    test_cases = [
        "ဒီနေ့ Pickup လေးရအုံးမလား",
        "Pick up ဘယ်အချိန်ရောက်မှာလဲ",
        "Pick up မရောက်သေးလို့ပါ"
    ]

    for text in test_cases:
        print(f"\n🔍 Testing: {text}")
        router_intent = test_router_intent(text)
        print(f"🎯 Router Intent: {router_intent}")
        
        if router_intent == "auto_pickup":
            extraction = test_pickup_extraction(text)
            print(f"📦 Extraction: {extraction}")
        elif router_intent == "auditor":
            print("✅ Routed to Auditor (Correct for status/complaint)")
        else:
            print(f"⚠️ Routed to: {router_intent}")
