import sys
import os
import json

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_utils

def test_pickup_extraction(text):
    extract_prompt = f"""
    Analyze the following message for a NEW pickup request or an inquiry about pickup availability.
    Message: "{text}"

    Output ONLY a JSON object with:
    - is_new_request: boolean (True if this is a request to pick up items OR an inquiry if pickup is available, False if it's a question about status of an existing order, complaint, or something else)
    - vehicle: "Bicycle" or "Car" (Default to null if not mentioned)
    - date_type: "today" or "tomorrow" (Default to "tomorrow" if not mentioned)
    - clean_remark: Extract ONLY the additional instructions, notes, or specific details (like quantity, amount, location, or special requests) from the message in Burmese, EXCLUDING the core pickup request phrase (e.g., "pick up လာယူပေးပါ", "လာကောက်ပေးပါ").
      Example 1: "မနက်ဖြန် pick up လေးလာပေးပါ၊ ငွေသား (၁၀)သိန်းယူခဲ့ပါ" -> "ငွေသား (၁၀)သိန်းယူခဲ့ပါ"
      Example 2: "ဒီနေ့ pick up လာယူပေးပါ၊ ပစ္စည်း (၅)ထုပ်ရှိပါတယ်" -> "ပစ္စည်း (၅)ထုပ်ရှိပါတယ်"
      If there are no additional instructions beyond the pickup request itself, return null.
    """
    ai_res_content = ai_utils.get_ai_completion(
        prompt=extract_prompt,
        model="google/gemini-3.1-flash-lite-preview",
        response_format={ "type": "json_object" },
        timeout=30.0
    )
    return json.loads(ai_res_content) if ai_res_content else None

if __name__ == "__main__":
    test_cases = [
        "မနက်ဖြန် pick up လေးလာပေးပါ၊ ငွေသား (၁၀)သိန်းယူခဲ့ပါ",
        "ဒီနေ့ pick up လာယူပေးပါ၊ ပစ္စည်း (၅)ထုပ်ရှိပါတယ်",
        "မနက်ဖြန် pick up လာပေးပါ"
    ]

    for text in test_cases:
        print(f"\n🔍 Testing: {text}")
        extraction = test_pickup_extraction(text)
        print(f"📦 Extraction: {json.dumps(extraction, indent=2, ensure_ascii=False)}")
