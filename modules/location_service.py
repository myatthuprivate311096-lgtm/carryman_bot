import requests
import time
import sys
import os

# Add parent directory to path to import ai_utils and logger
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from logger import log
    import ai_utils
except ImportError:
    # Fallback for direct execution or different path structures
    import ai_utils
    from logger import log

def search_location_osm(query):
    """
    Search for a location using Nominatim (OpenStreetMap) API.
    Returns the township, suburb, or city_district if found.
    """
    # Respect Nominatim's rate limit (1 request per second)
    time.sleep(1)
    
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "addressdetails": 1,
        "limit": 1
    }
    headers = {
        "User-Agent": "CarrymanBot/1.0"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if not data:
                log.info(f"📍 OSM: No results found for '{query}'")
                return None
            
            address = data[0].get("address", {})
            
            # Extract township, suburb, or city_district in order of preference
            township = address.get("township") or address.get("suburb") or address.get("city_district")
            
            if township:
                log.info(f"✅ OSM: Found '{township}' for '{query}'")
                return township
            else:
                log.info(f"⚠️ OSM: Location found but no township/suburb/city_district for '{query}'")
                return None
        else:
            log.error(f"❌ OSM API Error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        log.error(f"❌ OSM Exception for '{query}': {e}")
        return None

def get_location_with_fallback(query):
    """
    Get township for a location query.
    Tries OSM first, then falls back to AI.
    Returns (township, source)
    """
    # 1. Try OSM
    township = search_location_osm(query)
    if township:
        return township, "API"
    
    # 2. AI Fallback
    log.info(f"🤖 OSM failed. Trying AI fallback for '{query}'...")
    prompt = f"""
    You are a local geography expert in Myanmar. Given a location name, identify the corresponding Township in Yangon.
    Return ONLY the name of the township in English (e.g., 'Kamayut', 'Pabedan', 'Sanchaung').
    If you are absolutely unsure, return 'Unknown'.
    
    Location: {query}
    Township:"""
    
    try:
        ai_response = ai_utils.get_ai_completion(prompt)
        if ai_response and ai_response.lower() != "unknown":
            # Clean up response (remove extra words like "Township")
            clean_response = ai_response.strip().split('\n')[0].replace("Township", "").strip()
            log.info(f"✅ AI Fallback: Found '{clean_response}' for '{query}'")
            return clean_response, "AI"
    except Exception as e:
        log.error(f"❌ AI Fallback Exception for '{query}': {e}")
    
    return None, None

if __name__ == "__main__":
    # Quick test
    test_query = "Hledan"
    print(f"--- Testing OSM for '{test_query}' ---")
    result_osm = search_location_osm(test_query)
    print(f"OSM Result: {result_osm}")
    
    print(f"\n--- Testing Combined (get_location_with_fallback) for 'Junction City' ---")
    result_combined, source = get_location_with_fallback("Junction City")
    print(f"Combined Result: {result_combined} (Source: {source})")
