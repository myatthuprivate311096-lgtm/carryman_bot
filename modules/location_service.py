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
    Search for a location using Nominatim (OpenStreetMap) API with Yangon-first logic.
    Returns a formatted string 'Township, City' and a note if ambiguous.
    """
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "CarrymanBot/1.0"}
    
    def perform_request(q, limit=1):
        time.sleep(1) # Rate limit
        params = {"q": q, "format": "json", "addressdetails": 1, "limit": limit}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            log.error(f"❌ OSM Request Error: {e}")
            return []

    # Step 1: Search in Yangon, Myanmar
    yangon_query = f"{query}, Yangon, Myanmar"
    results = perform_request(yangon_query, limit=1)
    
    if results:
        addr = results[0].get("address", {})
        township = addr.get("township") or addr.get("suburb") or addr.get("city_district") or addr.get("city")
        city = addr.get("city") or addr.get("state") or "Yangon"
        if township:
            log.info(f"✅ OSM (Step 1): Found '{township}, {city}'")
            return f"{township}, {city}", None

    # Step 2: Fallback to Myanmar search
    log.info(f"📍 OSM: No Yangon result for '{query}'. Trying Myanmar fallback...")
    myanmar_query = f"{query}, Myanmar"
    results = perform_request(myanmar_query, limit=5) # Get more to check for ambiguity
    
    if not results:
        log.info(f"📍 OSM: No results found for '{query}' in Myanmar.")
        return None, None

    # Ambiguity Handling
    yangon_result = None
    other_cities = set()
    
    for res in results:
        addr = res.get("address", {})
        t = addr.get("township") or addr.get("suburb") or addr.get("city_district") or addr.get("city")
        c = addr.get("city") or addr.get("state") or addr.get("region")
        
        if not t: continue
        
        if c and "Yangon" in c:
            if not yangon_result:
                yangon_result = f"{t}, {c}"
        else:
            if c: other_cities.add(c)

    if yangon_result:
        note = f"(Note: Similar name exists in {', '.join(list(other_cities)[:2])})" if other_cities else None
        return yangon_result, note
    
    # If no Yangon result in Step 2, take the first Myanmar result
    addr = results[0].get("address", {})
    t = addr.get("township") or addr.get("suburb") or addr.get("city_district") or addr.get("city")
    c = addr.get("city") or addr.get("state") or addr.get("region")
    if t:
        return f"{t}, {c}", None

    return None, None

def get_location_with_fallback(query):
    """
    Get location for a query.
    Tries OSM first, then falls back to AI.
    Returns (location_string, source)
    """
    # 1. Try OSM
    loc_str, note = search_location_osm(query)
    if loc_str:
        final_str = f"{loc_str} {note}" if note else loc_str
        return final_str, "API"
    
    # 2. AI Fallback
    log.info(f"🤖 OSM failed. Trying AI fallback for '{query}'...")
    prompt = f"""
    You are a local geography expert in Myanmar. Given a location name, identify the corresponding Township in Yangon.
    Return ONLY the name of the township in English (e.g., 'Kamayut', 'Pabedan', 'Sanchaung').
    If you are absolutely unsure, return 'Unknown'.
    
    Location: {query}
    Township:"""
    
    try:
        ai_response = ai_utils.get_ai_completion(prompt, source='location_service')
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
