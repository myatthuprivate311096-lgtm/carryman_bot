# Version: 2.2 (Pickup/delivered date resolution + terminal remark handling)
import os
import re
import sys
import asyncio

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import db_manager
from logger import log
from modules.browser_manager import browser_manager
from modules import auto_login
import config

TRACKING_LIST_URL = config.TRACKING_LIST_URL

_STATUS_ARRIVAL_MARKERS = config.STATUS_TOPIC_MARKERS

_PHONE_NOT_IN_OS_MESSAGE = (
    "သင်အပ်ထားတဲ့ပါဆယ်များထဲတွင် ထိုဖုန်းနံပါတ်ဖြင့်မရှိပါသောကြောင့် "
    "Way Bill နံပါတ်လေးသိရမလားခင်ဗျ။ group ထဲတွင်ပို့ထားတဲ့ Pickup စာရင်းတွေကနေလည်း "
    "Way Bill နံပါတ်ကိုကြည့်လို့ရပါတယ်နော်"
)


def _normalize_os_name(name):
    if not name:
        return ""
    s = re.sub(r"\s+", " ", str(name).strip().lower())
    for ch in ("'", "'", "`", "’"):
        s = s.replace(ch, "")
    return s


def _os_names_match(order_os, mapped_os):
    a = _normalize_os_name(order_os)
    b = _normalize_os_name(mapped_os)
    return bool(a and b and a == b)


def extract_waybill(query):
    """Waybill number e.g. 260615028 (not customer phone 09xxx)."""
    if not query:
        return None
    text = str(query)
    compact = re.sub(r"\s+", "", text)
    for match in re.findall(r"\d{8,12}", compact):
        if re.match(r"^09\d{7,}$", match):
            continue
        if re.search(rf"09{re.escape(match)}", compact):
            continue
        return match
    return None


def extract_phone(query):
    """Customer phone 09xxxxxxxxx from query."""
    if not query:
        return None
    compact = re.sub(r"\s+", "", str(query))
    match = re.search(r"09\d{7,11}", compact)
    return match.group(0) if match else None


def _effective_tracking_query(query):
    """Context-merged query မှ လက်ရှိ မေးခွန်း အပိုင်းသာ ယူခြင်း。"""
    text = str(query or "").strip()
    for sep in ("follow-up:", "— correction:", " — "):
        if sep not in text:
            continue
        tail = text.split(sep)[-1].strip()
        if extract_phone(tail) or extract_waybill(tail):
            return tail
    return text


def _parse_status_query(query):
    """Extract search tokens from a /ai status question."""
    if not query:
        return {"search_terms": [], "status_hint": None, "waybill": None}

    text = _effective_tracking_query(query).strip()
    lowered = text.lower()
    search_terms = []

    waybill = extract_waybill(query)
    if waybill:
        search_terms.append(waybill)

    for match in re.findall(r"\b\d{4,}\b", text):
        if match == waybill:
            continue
        if re.match(r"^09\d{7,}$", match):
            continue
        if match not in search_terms:
            search_terms.append(match)
    for match in re.findall(r"09\d{7,11}", text):
        if match not in search_terms:
            search_terms.append(match)

    quoted = re.findall(r"[\"']([^\"']{2,})[\"']", text)
    search_terms.extend(quoted)

    if not search_terms:
        cleaned = re.sub(
            r"(/ai|ဝေးရောက်|ရောက်ပြီလား|ရောက်မှာလား|ရောက်မလား|status|tracking|အော်ဒါ|ပစ္စည်း|လား|လဲ|ပါ|နော်|ခင်ဗျ|ဗျ)",
            " ",
            lowered,
            flags=re.IGNORECASE,
        )
        for token in cleaned.split():
            token = token.strip(".,!?()")
            if len(token) >= 3 and not token.isdigit():
                search_terms.append(token)

    status_hint = None
    if any(k in lowered for k in ("onway", "ခရီးပေါ်", "လမ်းပေါ်")):
        status_hint = "ONWAY"
    elif any(k in lowered for k in ("delivered", "completed", "ရောက်ပြီ", "ရောက်ပြီး")):
        status_hint = "DELIVERED"

    deduped = []
    seen = set()
    for term in search_terms:
        key = term.lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(term)

    return {"search_terms": deduped[:3], "status_hint": status_hint, "waybill": waybill}


async def _click_os_text_mode(page):
    """Step 1: OS box ဘေးက 'A' (format-color-text) ခလုတ်。"""
    btn = page.locator("button:has(i.mdi-format-color-text)").first
    if await btn.count():
        await btn.click()
        await asyncio.sleep(0.5)
        return True
    return False


async def _fill_os_profile_name(page, os_name):
    """Step 2: OS name ရိုက်ပြီး select。"""
    if not os_name:
        return False
    try:
        await _click_os_text_mode(page)
        prof = page.locator("input[name='profileName']:not([type='hidden'])").last
        await prof.wait_for(state="visible", timeout=10000)
        await prof.click()
        await prof.fill(os_name)
        await asyncio.sleep(0.8)

        options = page.locator(".v-list-item__title, .v-list-item__content")
        count = await options.count()
        os_lower = os_name.lower()
        for i in range(count):
            text = (await options.nth(i).inner_text()).strip()
            if not text:
                continue
            if text.lower() == os_lower or os_lower in text.lower():
                await options.nth(i).click()
                await asyncio.sleep(0.5)
                return True

        await prof.press("Enter")
        await asyncio.sleep(0.5)
        return True
    except Exception as e:
        log.warning(f"⚠️ OS profileName filter failed: {e}")
        return False


async def _fill_tracking_search(page, value):
    """Step 3: Search box — customer phone or waybill。"""
    if not value:
        return False
    try:
        search_id = await page.evaluate(
            """() => {
                const inputs = [...document.querySelectorAll("input[type='text']")]
                    .filter(i => i.offsetWidth > 80);
                inputs.sort((a, b) => b.getBoundingClientRect().x - a.getBoundingClientRect().x);
                return inputs[0]?.id || null;
            }"""
        )
        if search_id:
            search = page.locator(f"#{search_id}")
        else:
            search = page.locator(
                "input[type='text']:not([name='profileName']):not([name='cityName'])"
                ":not([name='townshipName']):not([name='status'])"
            ).last
        await search.wait_for(state="visible", timeout=10000)
        await search.click()
        await search.fill(value)
        await asyncio.sleep(0.3)
        return True
    except Exception as e:
        log.warning(f"⚠️ Tracking search fill failed: {e}")
        return False


async def _click_magnify_search(page):
    """Step 4: Orange magnify search button。"""
    try:
        btn = page.locator("button:has(i.mdi-magnify)").first
        await btn.wait_for(state="visible", timeout=10000)
        await btn.click()
        await asyncio.sleep(2)
        return True
    except Exception as e:
        log.warning(f"⚠️ Magnify search click failed: {e}")
        return False


async def _ensure_logged_in(page):
    if "login" in page.url.lower():
        log.info("🔑 Session expired on tracking page — re-login...")
        success, msg = await auto_login.perform_login_on_page(page)
        if not success:
            return False, msg
    return True, "ok"


async def _fill_vuetify_select(page, label_text, value):
    if not value:
        return False

    try:
        inp = page.locator(
            f"label:has-text('{label_text}')"
        ).locator("xpath=ancestor::div[contains(@class,'v-input')]//input").first
        await inp.wait_for(state="visible", timeout=10000)
        await inp.click()
        await asyncio.sleep(0.4)
        await inp.fill(value)
        await asyncio.sleep(0.8)

        options = page.locator(".v-list-item__title, .v-list-item__content, [role='option']")
        count = await options.count()
        value_lower = value.lower()

        for i in range(count):
            text = (await options.nth(i).inner_text()).strip()
            if not text:
                continue
            if text.lower() == value_lower or value_lower in text.lower():
                await options.nth(i).click()
                await asyncio.sleep(0.5)
                return True

        await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)
        return True
    except Exception as e:
        log.warning(f"⚠️ Vuetify select fill failed for {label_text}: {e}")
        return False


async def _fill_search_box(page, value):
    if not value:
        return False
    try:
        search = page.locator(
            "label:has-text('Search')"
        ).locator("xpath=ancestor::div[contains(@class,'v-input')]//input").first
        await search.wait_for(state="visible", timeout=10000)
        await search.fill(value)
        await search.press("Enter")
        await asyncio.sleep(1.5)
        return True
    except Exception as e:
        log.warning(f"⚠️ Search box fill failed: {e}")
        return False


async def _wait_for_table_rows(page, timeout_sec=20):
    for _ in range(timeout_sec):
        rows = await page.locator("tbody tr").count()
        if rows > 0:
            return rows
        await asyncio.sleep(1)
    return 0


def _parse_api_tracking_results(data):
    """Parse /api/v1/order/trackinglist/search JSON response."""
    orders = []
    if not isinstance(data, list):
        return orders

    for entry in data:
        item = entry.get("itemDto") or {}
        if not item.get("voucherCode") and not item.get("phone"):
            continue
        order_dto = item.get("orderDto") or {}
        os_dto = order_dto.get("osAccountDto") or {}
        ts = item.get("townshipDto") or {}
        rider = item.get("riderAccountDto") or {}
        assign = item.get("assignRiderAccountDto") or {}
        collector = rider.get("profileName") or ""
        deliverer = assign.get("profileName") or ""
        status = item.get("status", "")
        pickup_date = _resolve_pickup_date(item, order_dto)
        received_date = _normalize_api_date(item.get("receivedDate") or order_dto.get("receivedDate") or "")
        delivered_date = _normalize_api_date(item.get("deliveredDate") or item.get("tbfDeliveredDate") or "")
        status_date = _resolve_status_date(item, order_dto, status)
        orders.append({
            "waybill": item.get("voucherCode", ""),
            "status": status,
            "phone": item.get("phone", ""),
            "township": ts.get("townshipName", ""),
            "os_name": os_dto.get("profileName", ""),
            "receiver": item.get("customerName", ""),
            "collector": collector,
            "deliverer": deliverer,
            "order_id": str(item.get("itemId", "")),
            "pickup_date": pickup_date,
            "received_date": received_date,
            "delivered_date": delivered_date,
            "status_date": status_date,
            "remark": item.get("remark") or order_dto.get("remark") or "",
            "customer_get": entry.get("customerGet", 0) or 0,
            "customer_paid": entry.get("customerPaid", 0) or 0,
            "os_to_pay": entry.get("osToPay", 0) or 0,
            "raw": str(entry)[:300],
        })
    return orders


def _parse_date_key(date_text):
    """Sort key for dd-mm-yyyy dates."""
    if not date_text:
        return (0, 0, 0)
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", str(date_text).strip())
    if not m:
        return (0, 0, 0)
    return (int(m.group(3)), int(m.group(2)), int(m.group(1)))


def _pick_latest_waybill_order(orders):
    if not orders:
        return None
    if len(orders) == 1:
        return orders[0]
    return max(orders, key=lambda o: _parse_date_key(o.get("pickup_date")))


def _fmt_mmk(amount):
    try:
        return f"{int(float(amount)):,}"
    except (TypeError, ValueError):
        return str(amount or 0)


_DEFAULT_STATUS_MEANINGS = {
    "COLLECTED": "ပစ္စည်းကို လက်ခံရရှိပြီးပါပြီ — ဝေးမြို့သို့ ပို့ဆောင်ရန် စီစဉ်နေဆဲဖြစ်နိုင်ပါတယ်",
    "FINISHED": "ပစ္စည်းရောက်ပြီးသားပါခင်ဗျ",
    "ONWAY": "ပစ္စည်းက ခရီးပေါ်မှာရှိနေပါတယ်ခင်ဗျ",
    "ASSIGNED": "ပစ္စည်းကို စီစဉ်ပြီးပြီး ပို့ဆောင်ရန် တာဝန်ပေးထားပါတယ်",
    "PENDING": "ပစ္စည်းကို စီစဉ်နေဆဲဖြစ်ပါတယ်",
    "PICKUP": "လာကောက်ရမည့်အဆင့်မှာ ရှိနေပါတယ်",
    "RETURN": "ပြန်ပို့ရန်အဆင့်မှာ ရှိနေပါတယ်",
}

_TERMINAL_STATUSES = frozenset({
    "DELIVERED", "COMPLETED", "FINISHED", "RETURNFINISHED",
})

_REMARK_CODE_RE = re.compile(r"cc|uh|po|os\d+|\d+d|\d+c", re.IGNORECASE)

_MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _normalize_api_date(val):
    if not val:
        return ""
    s = str(val).strip()
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        month, day, year = m.group(1), m.group(2), m.group(3)
        return f"{day}-{month}-{year}"
    return s


def _pickup_date_from_waybill(waybill):
    """Waybill YYMMDDxxx → dd-mm-yyyy pickup hint."""
    wb = str(waybill or "").strip()
    m = re.match(r"(\d{2})(\d{2})(\d{2})", wb)
    if not m:
        return ""
    yy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{dd:02d}-{mm:02d}-{2000 + yy}"


def _resolve_pickup_date(item, order_dto):
    waybill = item.get("voucherCode") or ""
    for src in (
        item.get("pickupDate"),
        order_dto.get("pickupDate"),
        item.get("tbfPickupDate"),
        order_dto.get("tbfPickupDate"),
        order_dto.get("receivedDate"),
        _pickup_date_from_waybill(waybill),
    ):
        normalized = _normalize_api_date(src)
        if normalized:
            return normalized
    return ""


def _resolve_status_date(item, order_dto, status):
    """Date paired with conversational status line."""
    key = str(status or "").strip().upper()
    if key in _TERMINAL_STATUSES:
        for src in (item.get("deliveredDate"), item.get("tbfDeliveredDate")):
            normalized = _normalize_api_date(src)
            if normalized:
                return normalized
    if key == "ONWAY":
        for src in (item.get("assignedDate"), item.get("receivedDate")):
            normalized = _normalize_api_date(src)
            if normalized:
                return normalized
    for src in (item.get("receivedDate"), order_dto.get("receivedDate")):
        normalized = _normalize_api_date(src)
        if normalized:
            return normalized
    return ""


def _extract_remark_codes(raw):
    if not raw:
        return []
    return [c.lower() for c in _REMARK_CODE_RE.findall(str(raw))]


def _os_code_display_date(os_code, ref_date_ddmmyyyy):
    m = re.match(r"os(\d+)", str(os_code or "").lower())
    if not m:
        return ""
    day = int(m.group(1))
    dm = re.match(r"(\d{2})-(\d{2})-(\d{4})", str(ref_date_ddmmyyyy or "").strip())
    if not dm:
        return ""
    _, month, year = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
    abbr = _MONTH_ABBR.get(month, str(month))
    return f"{day}-{abbr}-{year}"


def _build_remark_line(raw, remark_map, status, pickup_date, status_date):
    """Terminal statuses: only friendly coded remarks (e.g. ccos9), never raw text."""
    if not raw or not str(raw).strip():
        return ""

    codes = _extract_remark_codes(raw)
    codes_upper = [c.upper() for c in codes]
    ref_date = pickup_date or status_date

    has_cc = "CC" in codes_upper
    os_code = next((c for c in codes_upper if re.match(r"OS\d+", c)), None)

    if has_cc and os_code:
        os_date = _os_code_display_date(os_code, ref_date)
        if os_date:
            return (
                f"ဆက်သွယ်မရကြောင်း ({os_date}) မှာ OS သို့ "
                "အကြောင်းကြားထားတာလေးရှိပါတယ်နော်"
            )

    if str(status or "").strip().upper() in _TERMINAL_STATUSES:
        return ""

    expanded = _expand_remark_text(raw, remark_map)
    return f"မှတ်ချက်: {expanded}" if expanded else ""


def _lookup_status_meaning(status, status_map):
    key = str(status or "").strip().upper()
    if not key:
        return "အခြေအနေကို ယာယီမသိရသေးပါ"
    if key in status_map:
        return status_map[key]
    if key in _DEFAULT_STATUS_MEANINGS:
        return _DEFAULT_STATUS_MEANINGS[key]
    return f"လက်ရှိ status က {key} ဖြစ်နေပါတယ်"


def _expand_remark_text(remark, remark_map):
    if not remark or not str(remark).strip():
        return ""
    raw = str(remark).strip()
    if not remark_map:
        return raw

    expanded = []
    for token in _extract_remark_codes(raw):
        meaning = remark_map.get(token.upper())
        if meaning:
            expanded.append(meaning)
    for token in re.split(r"[,;/\s]+", raw):
        token = token.strip()
        if not token or _REMARK_CODE_RE.fullmatch(token):
            continue
        meaning = remark_map.get(token.upper()) or remark_map.get(token)
        expanded.append(meaning or token)

    if expanded:
        return "၊ ".join(expanded)
    return raw


def _build_payment_lines(order):
    lines = []
    cust_get = order.get("customer_get") or 0
    cust_paid = order.get("customer_paid") or 0
    os_to_pay = order.get("os_to_pay") or 0

    try:
        cust_get_i = int(float(cust_get))
        cust_paid_i = int(float(cust_paid))
        os_to_pay_i = int(float(os_to_pay))
    except (TypeError, ValueError):
        return lines

    if cust_get_i > 0:
        lines.append(f"Customer ဆီက ကောက်ခံရမည့်ပမာဏ {_fmt_mmk(cust_get_i)} ကျပ် ဖြစ်ပါတယ်")
    if cust_paid_i > 0:
        lines.append(f"Customer ဘက်က {_fmt_mmk(cust_paid_i)} ကျပ် ပေးချေပြီးပါပြီ")
    if os_to_pay_i < 0:
        lines.append(f"OS ကို {_fmt_mmk(abs(os_to_pay_i))} ကျပ် ပေးရန်ကျန်ပါတယ်")
    elif os_to_pay_i > 0:
        lines.append(f"OS ဆီမှ {_fmt_mmk(os_to_pay_i)} ကျပ် ပြန်ရရန်ရှိပါတယ်")
    return lines


def _format_status_conversational(status, status_meaning, received_date):
    """Combine status + received date into one CS-style Myanmar sentence."""
    key = str(status or "").strip().upper()
    has_date = bool(received_date and received_date != "-")

    phrases = {
        "COLLECTED": ("စာရင်းသွင်းပြီးပါတယ်", "စာရင်းသွင်းပြီးသားပါတယ်"),
        "FINISHED": ("ရောက်ပြီးပါတယ်", "ပစ္စည်းရောက်ပြီးသားပါတယ်"),
        "ASSIGNED": ("ပို့ဆောင်ရန် တာဝန်ပေးထားပါတယ်", "ပို့ဆောင်ရန် တာဝန်ပေးထားပါတယ်"),
        "PENDING": ("စီစဉ်နေဆဲပါတယ်", "စီစဉ်နေဆဲပါတယ်"),
        "PICKUP": ("လာကောက်ရမည့်အဆင့်မှာ ရှိနေပါတယ်", "လာကောက်ရမည့်အဆင့်မှာ ရှိနေပါတယ်"),
        "RETURN": ("ပြန်ပို့ရန်အဆင့်မှာ ရှိနေပါတယ်", "ပြန်ပို့ရန်အဆင့်မှာ ရှိနေပါတယ်"),
    }

    if key == "ONWAY":
        if has_date:
            return f"{received_date} ရက်နေ့လေးကတည်းက ခရီးပေါ်မှာ ရှိနေပါတယ်ခင်ဗျ။"
        return "ပစ္စည်းက ခရီးပေါ်မှာ ရှိနေပါတယ်ခင်ဗျ။"

    if key in phrases:
        with_date, without_date = phrases[key]
        body = with_date if has_date else without_date
        if has_date:
            return f"{received_date} ရက်နေ့လေးမှာ {body}ခင်ဗျ။"
        return f"{body}ခင်ဗျ။"

    meaning = (status_meaning or "အခြေအနေကို ယာယီမသိရသေးပါ").rstrip("။")
    if has_date:
        return f"{received_date} ရက်နေ့လေးမှာ {meaning}ခင်ဗျ။"
    if meaning.endswith("ခင်ဗျ"):
        return f"{meaning}။"
    return f"{meaning}ခင်ဗျ။"


def format_tracking_reply(orders, os_name=None, search_terms=None):
    if not orders:
        terms = ", ".join(search_terms or [])
        os_part = f" ({os_name})" if os_name else ""
        if terms:
            return (
                f"စစ်ကြည့်ပြီးပါပြီ{os_part} — '{terms}' နဲ့ ကိုက်ညီတဲ့ ပစ္စည်းကို "
                "tracking ထဲမှာ မတွေ့ရသေးပါဘူးခင်ဗျ။ Way Bill သို့မဟုတ် ဖုန်းနံပါတ် ပြန်စစ်ပေးပါ။"
            )
        return "tracking ထဲမှာ ရှာဖွေဖို့ Way Bill သို့မဟုတ် ဖုန်းနံပါတ် ပေးပါခင်ဗျ။"

    status_map = db_manager.get_status_meaning_map()
    remark_map = db_manager.get_remark_meaning_map()

    order = _pick_latest_waybill_order(orders)
    if not order:
        return "tracking ဒေတာကို ယာယီ မဖတ်ရသေးပါဘူးခင်ဗျ။"

    waybill = order.get("waybill") or "-"
    receiver = order.get("receiver") or "-"
    township = order.get("township") or "-"
    status = str(order.get("status") or "").strip().upper()
    status_meaning = _lookup_status_meaning(status, status_map)
    pickup_date = order.get("pickup_date") or "-"
    status_date = order.get("status_date") or order.get("received_date") or "-"
    remark_line = _build_remark_line(
        order.get("remark"), remark_map, status, pickup_date, status_date,
    )
    os_label = order.get("os_name") or ""

    lines = [
        f"Waybill {waybill} (Pickup Date: {pickup_date}) အတွက် စစ်ပေးလိုက်ပါပြီ ခင်ဗျ။",
        f"{receiver} — {township} သို့ ပို့ထားတဲ့ ပါဆယ်ပါ။",
    ]
    if os_label:
        lines.append(f"OS: {os_label}")

    lines.append(_format_status_conversational(status, status_meaning, status_date))

    if remark_line:
        lines.append(remark_line)

    lines.extend(_build_payment_lines(order))

    return "\n".join(lines)


async def _search_tracking_via_api(page, search_value):
    """Trigger UI search and capture trackinglist/search API response."""
    if not search_value:
        return []

    await _fill_tracking_search(page, search_value)
    try:
        search_id = await page.evaluate(
            """() => {
                const inputs = [...document.querySelectorAll("input[type='text']")]
                    .filter(i => i.offsetWidth > 80);
                inputs.sort((a, b) => b.getBoundingClientRect().x - a.getBoundingClientRect().x);
                return inputs[0]?.id || null;
            }"""
        )
        search = page.locator(f"#{search_id}") if search_id else page.locator("input[type='text']").last

        async with page.expect_response(
            lambda r: "trackinglist/search" in r.url and search_value in r.url,
            timeout=25000,
        ) as resp_info:
            await search.press("Enter")
            await _click_magnify_search(page)
        response = await resp_info.value
        if response.ok:
            data = await response.json()
            orders = _parse_api_tracking_results(data)
            if orders:
                log.info(f"✅ trackinglist/search returned {len(orders)} row(s) for {search_value}")
                return orders
            log.warning(f"⚠️ trackinglist/search empty for {search_value}")
    except Exception as e:
        log.warning(f"⚠️ trackinglist/search API capture failed: {e}")

    # DOM fallback
    await _click_magnify_search(page)
    await _wait_for_table_rows(page, timeout_sec=12)
    orders = await _extract_order_rows(page, limit=10)
    return [row for row in orders if _row_matches_terms(row, [search_value])]


async def _extract_order_rows(page, limit=5):
    rows = []
    try:
        raw_rows = await page.evaluate(
            """(limit) => {
                const headers = [...document.querySelectorAll('th')]
                    .map(h => h.innerText.trim())
                    .filter(Boolean);
                return [...document.querySelectorAll('tbody tr')]
                    .slice(0, limit)
                    .map(tr => {
                        const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                        const obj = { raw: cells.join(' | ') };
                        headers.forEach((h, i) => {
                            if (cells[i]) obj[h] = cells[i];
                        });
                        return obj;
                    });
            }""",
            limit,
        )
        for item in raw_rows or []:
            rows.append({
                "raw": item.get("raw", ""),
                "waybill": item.get("Waybill", "") or item.get("Code", ""),
                "status": item.get("Status", ""),
                "phone": item.get("Phone", ""),
                "township": item.get("Township", ""),
                "os_name": item.get("OS Name", ""),
                "receiver": item.get("Receiver Name", ""),
                "collector": item.get("Collector", ""),
                "deliverer": item.get("Deliverer", ""),
                "order_id": item.get("Item Id", "") or item.get("No.", ""),
                "pickup_date": item.get("Pickup Date", "") or item.get("Received Date", ""),
                "received_date": item.get("Received Date", ""),
                "remark": item.get("Remark", ""),
                "customer_get": 0,
                "customer_paid": 0,
                "os_to_pay": 0,
            })
    except Exception as e:
        log.warning(f"⚠️ Tracking row extract failed: {e}")
    return rows


def _pick_cell(cells, primary_idx, fallback_index=None):
    if primary_idx < len(cells) and cells[primary_idx]:
        return cells[primary_idx]
    if fallback_index is not None and fallback_index < len(cells):
        return cells[fallback_index]
    return ""


def _row_matches_terms(order, terms):
    if not terms:
        return True
    haystack = " ".join(
        [
            order.get("order_id", ""),
            order.get("waybill", ""),
            order.get("phone", ""),
            order.get("os_name", ""),
            order.get("receiver", ""),
            order.get("address", ""),
            order.get("township", ""),
            order.get("remark", ""),
            order.get("raw", ""),
        ]
    ).lower()
    return any(term.lower() in haystack for term in terms)


async def _query_tracking_task(page, os_name, search_terms, status_hint=None, waybill=None, mapped_os_name=None):
    try:
        await page.goto(TRACKING_LIST_URL, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        ok, msg = await _ensure_logged_in(page)
        if not ok:
            return False, f"Login မအောင်မြင်ပါ: {msg}"

        if "login" in page.url.lower():
            await page.goto(TRACKING_LIST_URL, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2)

        search_value = waybill or (search_terms[-1] if search_terms else "")
        if not search_value:
            answer = format_tracking_reply([], os_name=os_name, search_terms=search_terms)
            return True, answer

        # Waybill / phone search → global website search; OS scope applied after results.
        is_phone_search = bool(extract_phone(search_value))
        is_waybill_search = bool(waybill)
        if os_name and not is_waybill_search and not is_phone_search:
            await _fill_os_profile_name(page, os_name)

        await _fill_tracking_search(page, search_value)
        orders = await _search_tracking_via_api(page, search_value)

        match_terms = [waybill] if waybill else search_terms
        if match_terms and orders:
            orders = [row for row in orders if _row_matches_terms(row, match_terms)]

        if mapped_os_name and orders:
            owned_orders = [row for row in orders if _os_names_match(row.get("os_name"), mapped_os_name)]
            if not owned_orders:
                log.info(
                    f"🚫 OS scope mismatch for '{mapped_os_name}' — "
                    f"found {[o.get('os_name') for o in orders[:3]]}"
                )
                return True, _PHONE_NOT_IN_OS_MESSAGE
            orders = owned_orders

        answer = format_tracking_reply(
            orders,
            os_name=os_name,
            search_terms=match_terms or search_terms,
        )
        return True, answer
    except Exception as e:
        log.error(f"❌ Tracking query failed: {e}")
        return False, str(e)


def query_live_tracking(chat_id, query):
    """Synchronous entry for /ai status topic."""
    parsed = _parse_status_query(query)
    search_terms = parsed.get("search_terms") or []
    status_hint = parsed.get("status_hint")
    waybill = parsed.get("waybill")
    phone = extract_phone(_effective_tracking_query(query))
    if not phone:
        for term in search_terms:
            phone = extract_phone(term)
            if phone:
                break

    mapped_os_name = db_manager.get_group_website_os_name(chat_id)

    os_name = None
    if not waybill and not phone:
        os_name = mapped_os_name

    state_path = os.path.join(BASE_DIR, "state.json")
    try:
        return browser_manager.run_task(
            _query_tracking_task,
            storage_state=state_path,
            os_name=os_name,
            search_terms=search_terms,
            status_hint=status_hint,
            waybill=waybill,
            mapped_os_name=mapped_os_name if (phone or waybill) else None,
        )
    except Exception as e:
        log.error(f"❌ query_live_tracking error: {e}")
        return False, str(e)


def is_status_arrival_question(query):
    if not query:
        return False
    lowered = str(query).lower()
    if any(marker in lowered for marker in _STATUS_ARRIVAL_MARKERS):
        return True
    if re.search(r"\b\d{4,}\b", query) or re.search(r"09\d{7,11}", query):
        return True
    return False


def check_order_status(order_id):
    """Backward-compatible single order lookup."""
    return query_live_tracking(0, f"order {order_id}")


def run(data, event):
    if event == "check_status":
        order_id = data.get("order_id")
        if not order_id:
            return False, "Missing order_id in data"
        return check_order_status(order_id)
    return False, f"Unknown event: {event}"


def handle(bot, message):
    """Auto-route disabled — status answers are manual /ai only."""
    log.info(
        f"🔇 check_order auto-route ignored for msg {message.message_id}. Use /ai for live tracking."
    )
    return False


if __name__ == "__main__":
    success, message = query_live_tracking(0, "09787176081 ရောက်ပြီလား")
    print(f"Result: {success}, Message: {message}")
