# Version: 1.4 (tracking pickup/delivered dates + ccos remark expansion)
import os
import re
import json
import importlib
import db_manager
from dotenv import load_dotenv
from logger import log
import ai_utils
import config

# 💡 Absolute Path Fix
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

SANDBOX_CHAT_ID = config.SANDBOX_CHAT_ID

_DELIVERY_TOPIC_MARKERS = config.DELIVERY_TOPIC_MARKERS
_STATUS_TOPIC_MARKERS = config.STATUS_TOPIC_MARKERS


def _is_ai_globally_enabled(chat_id):
    """global_ai OFF ဖြစ်ရင် Sandbox group တစ်ခုတည်းသာ ခွင့်ပြု。"""
    if chat_id == SANDBOX_CHAT_ID:
        return True
    return db_manager.get_ai_global_status() == 'ON'


def register_ai_handler(bot):
    """Register /ai BEFORE catch-all message handlers (telebot first-match wins)."""
    @bot.message_handler(commands=['ai'])
    def handle_ai_command(message):
        log.info(
            f"🤖 /ai command in chat {message.chat.id} "
            f"topic {getattr(message, 'message_thread_id', None)} from {message.from_user.id}"
        )
        handle_ai_query(bot, message, is_automatic=False)

    register_ai_feedback_handlers(bot)


AI_FEEDBACK_REASON_LABELS = db_manager.AI_FEEDBACK_REASON_LABELS

def is_ai_office_hours():
    """
    AI Auto-Answer အလုပ်လုပ်မည့်အချိန် (၂၄ နာရီ ဖွင့်ထားသည်)
    အစ်ကို့တောင်းဆိုချက်အရ Private Chat AI Reply အတွက် ၂၄ နာရီ ဖွင့်ပေးထားခြင်း ဖြစ်ပါသည်။
    """
    return True

def _looks_like_delivery_support_question(text):
    """Delivery/pricing/location service questions — reply only via manual /ai."""
    if not text:
        return False
    t = text.strip().lower()

    pickup_markers = (
        'pick up', 'pickup', 'pick-up',
        'လာယူ', 'လာကောက်', 'တင်ပေးပါ', 'ခေါ်ပေးပါ',
        'လာခဲ့ပေးပါ', 'လာယူပေးပါ', 'လာကောက်ပေးပါ',
    )
    if any(marker in t for marker in pickup_markers):
        return False

    delivery_markers = (
        'ပို့လား', 'ပို့လို့', 'ပို့ရ', 'ပို့နိုင်', 'ပို့ခ',
        'တန်ဆာခ', 'cod',
        'မြို့နယ်လဲ', 'ဘယ်မြို့နယ်', 'ဘယ်မြို့နယ်ထဲ',
        'ပြန်ရလား', 'ပို့လား', 'ရောက်လား', 'ရောက်မလား',
        'home delivery', 'gate drop',
    )
    return any(marker in t for marker in delivery_markers)


def _normalize_ai_query(text):
    """/ai query မှ noise (quotes, command prefix) ဖယ်ရှားခြင်း。"""
    if not text:
        return ""
    cleaned = str(text).replace('/ai', '').strip()
    return cleaned.strip('"').strip("'").strip()


_AMBIGUOUS_FOLLOWUP_MARKERS = (
    "ဘယ်လောက်", "ကြာမှ", "ရလား", "ရမလား", "ပို့ခ", "တန်ဆာခ",
    "cod", "ရဲ့", "သူ", "ဒီ", "အဲ့", "ဟို", "နဲ့လဲ", "ဘာလဲ",
    "how much", "how long", "fee",
)


def _is_correction_followup(query):
    """Customer က အရင်အဖြေကို ပြင်ပြောနေခြင်း သို့မဟုတ် မဟုတ်ဘူးလို့ ပြောခြင်း。"""
    if not query:
        return False
    q = str(query)
    markers = (
        "မဟုတ်", "မဟုတ်ပါ", "မဟုတ်ဘူး", "မှားတယ်", "မှားပါတယ်",
        "သင့်မတော်", "wrong", "not that", "ပြန်စစ်", "နောက်တစ်ခု",
    )
    return any(m in q.lower() if m.isascii() else m in q for m in markers)


def _query_has_explicit_tracking_target(query):
    """ဖုန်း (09) သို့မဟုတ် waybill ပါဝင်သော tracking မေးခွန်း。"""
    from modules import check_order
    if check_order.extract_waybill(query):
        return True
    if check_order.extract_phone(query):
        return True
    return False


def _query_has_explicit_location(query, user_level=1):
    """မေးခွန်းထဲမှာ မြို့နယ်/နေရာ ရှင်းရှင်းပါဝင်မှု。"""
    if not query:
        return False
    if _query_has_explicit_tracking_target(query):
        return True
    if db_manager.search_location_delivery(query, user_level):
        return True
    return False


def _is_ambiguous_followup(query, user_level=1):
    """နေရာ/ဝေး မပါဘဲ ဆက်မေးတဲ့ အတိုမေးခွန်း。"""
    if not query:
        return False
    q = str(query).strip()
    if len(q) > 100:
        return False
    if _query_has_explicit_tracking_target(q):
        return False
    if db_manager.search_location_delivery(q, user_level):
        return False
    lowered = q.lower()
    return any(m in lowered if m.isascii() else m in q for m in _AMBIGUOUS_FOLLOWUP_MARKERS)


def _looks_like_status_question(text):
    if not text:
        return False
    lowered = str(text).lower()
    if any(marker in lowered for marker in _STATUS_TOPIC_MARKERS):
        return True
    if re.search(r"\b\d{8,12}\b", text) or re.search(r"09\d{7,11}", text):
        return True
    return False


def _resolve_ai_search_query(user_id, chat_id, query, user_level=1):
    """
    Context-aware query expansion (per user_id + chat_id, 1 hour).
    Returns: (search_query, is_correction, needs_clarification)
    """
    if not query:
        return query, False, False

    turns = db_manager.get_ai_conversation_turns(user_id, chat_id)
    last_turn = turns[-1] if turns else None
    is_correction = _is_correction_followup(query)

    # အသက် phone/waybill ပါရင် အဟောင်း context မပေါင်းဘဲ လက်ရှိ query သာ သုံး
    if _query_has_explicit_tracking_target(query):
        from modules import check_order
        new_phone = check_order.extract_phone(query)
        new_wb = check_order.extract_waybill(query)
        if last_turn:
            last_q = last_turn.get("query") or ""
            last_phone = check_order.extract_phone(last_q)
            last_wb = last_turn.get("waybill") or check_order.extract_waybill(last_q)
            if (new_phone and last_phone and new_phone != last_phone) or (
                new_wb and last_wb and str(new_wb) != str(last_wb)
            ):
                log.info(f"🔀 New tracking target — skip context merge (user {user_id})")
        return query, False, False

    if db_manager.search_location_delivery(query, user_level) and last_turn:
        new_loc = db_manager.search_location_delivery(query, user_level)
        new_id = _extract_location_id(new_loc)
        old_id = (last_turn or {}).get("location_id") or ""
        if new_id and old_id and new_id != old_id:
            log.info(f"🔀 Topic change detected: location {old_id} → {new_id}")
            return query, False, False

    if is_correction and last_turn:
        prev_q = last_turn.get("query") or ""
        for turn in reversed(turns):
            if turn.get("location_id") or turn.get("location_label"):
                prev_q = turn.get("query") or prev_q
                break
        if prev_q and prev_q.strip() not in query:
            merged = f"{prev_q} — {query}"
            log.info(f"🔗 Merged AI correction context for user {user_id} chat {chat_id}")
            return merged, True, False
        return query, True, False

    if _is_ambiguous_followup(query, user_level):
        if not last_turn:
            return query, False, True
        prev_q = last_turn.get("query") or ""
        for turn in reversed(turns):
            if turn.get("location_id") or turn.get("location_label"):
                prev_q = turn.get("query") or prev_q
                break
        if prev_q:
            merged = f"{prev_q} — follow-up: {query}"
            log.info(f"🔗 Merged ambiguous follow-up for user {user_id} chat {chat_id}")
            return merged, False, False
        return query, False, True

    return query, False, False


def _extract_location_id(location_context):
    if not location_context:
        return None
    match = re.search(r"Location ID:\s*(\S+)", str(location_context))
    return match.group(1) if match else None


def _extract_location_label(location_context):
    if not location_context:
        return ""
    mm_match = re.search(r"\(([^)]+)\)", str(location_context))
    if mm_match:
        return mm_match.group(1).strip()
    township = re.search(r"Township:\s*([^|]+)", str(location_context), re.IGNORECASE)
    return township.group(1).strip() if township else ""


def _build_conversation_block(turns):
    if not turns:
        return ""
    lines = ["[Recent Conversation (same customer, last 1 hour)]:"]
    for idx, turn in enumerate(turns, start=1):
        lines.append(f"{idx}. Customer asked: {turn.get('query', '')}")
        if turn.get("reply_summary"):
            lines.append(f"   Bot answered: {turn.get('reply_summary', '')}")
        if turn.get("location_label"):
            lines.append(f"   Location context: {turn.get('location_label')}")
    lines.append(
        "Use this when the latest message is a short follow-up or correction. "
        "If the customer clearly changes location/topic, ignore old context."
    )
    return "\n".join(lines)


def _summarize_reply_for_context(answer, max_len=220):
    if not answer:
        return ""
    text = re.sub(r"\s+", " ", str(answer).strip())
    return text[:max_len]


def _record_ai_turn(user_id, chat_id, query, answer, topic, location_context=None, waybill=None):
    db_manager.append_ai_conversation_turn(
        user_id,
        chat_id,
        query,
        _summarize_reply_for_context(answer),
        topic=topic,
        location_id=_extract_location_id(location_context),
        location_label=_extract_location_label(location_context),
        waybill=waybill,
    )


def _classify_ai_topic(query):
    """
    Topic gate for manual /ai only.
    - status_arrival_topic: live website tracking + OS filter
    - delivery_info_topic: Google Sheet grounded delivery Q&A
    """
    if not query:
        return "delivery_info"

    lowered = str(query).lower()
    has_delivery = any(marker in lowered for marker in _DELIVERY_TOPIC_MARKERS)
    has_status = any(marker in lowered for marker in _STATUS_TOPIC_MARKERS)

    if re.search(r"\b\d{4,}\b", query) or re.search(r"09\d{7,11}", query):
        has_status = True
    if re.search(r"\bP\d{6,}\b", query, re.IGNORECASE):
        has_status = True

    if has_status and not has_delivery:
        return "status_arrival"
    return "delivery_info"


def _is_allowed_private_customer_query(query, user_level):
    """
    Private non-staff users may only ask:
    - delivery / Google Sheet knowledge questions
    - waybill / order-id / phone based tracking (ဝေးလမ်းကြောင်း)
  """
    if not query or not str(query).strip():
        return False

    topic = _classify_ai_topic(query)
    if topic == "status_arrival":
        return True

    lowered = str(query).lower()
    if any(marker in lowered for marker in _DELIVERY_TOPIC_MARKERS):
        return True

    if re.search(r"\bP\d{6,}\b", query, re.IGNORECASE):
        return True

    try:
        if db_manager.search_location_delivery(query, user_level):
            return True
        if db_manager.search_knowledge(query, user_level):
            return True
    except Exception as e:
        log.warning(f"⚠️ Sheet pre-check failed for private /ai gate: {e}")

    return False


def _handle_private_scope_violation(bot, message, user_id):
    """Three-strike rule for private non-staff out-of-scope /ai usage."""
    count = db_manager.increment_out_of_scope(user_id)

    if count < 3:
        bot.reply_to(
            message,
            "တောင်းပန်ပါတယ်ခင်ဗျာ။ CarryMan delivery၊ waybill/tracking၊ "
            "နှင့် Google Sheet ထဲရှိ ဝန်ဆောင်မှုဆိုင်ရာ မေးခွန်းများကိုသာ ဖြေပေးနိုင်ပါတယ်။ "
            "အခြားအကြောင်းအရာများကို မဖြေကြားနိုင်ပါဘူးခင်ဗျာ။"
        )
        return

    bot.reply_to(
        message,
        "admin ဆီကိုအကြောင်းကြားပေးထားတာမို့ ရုံးချိန်အတွင်းအမြန်ဆုံးဆက်သွယ်ပေးပါလိမ့်မယ်နော်"
    )

    admin_chat = -1003601049225
    topic_id = 920
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"
    alert_text = (
        f"⚠️ **Human Support Needed!**\nA user ({username}) has asked out-of-scope "
        f"/ai questions 3 times in Private Chat. AI reply is now paused for them."
    )

    from telebot import types
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔓 Unmute AI", callback_data=f"unmute_user:{user_id}"))

    try:
        bot.send_message(admin_chat, alert_text, message_thread_id=topic_id, reply_markup=markup)
    except Exception as ae:
        log.error(f"❌ Failed to send Strike 3 alert to admin: {ae}")
        bot.send_message(admin_chat, alert_text)

    db_manager.set_human_intervention(user_id, 1)
    log.info(f"🚫 User {user_id} muted after 3 private /ai scope strikes.")


def _format_rag_fallback_answer(rag_context):
    """AI API မအောင်မြင်ရင် Sheet data ကနေ တိုက်ရိုက်ဖြေကြားခြင်း。"""
    if not rag_context:
        return None
    answers = []
    seen = set()
    for block in rag_context.split('---\n'):
        if 'Answer:' not in block:
            continue
        answer = block.split('Answer:', 1)[1].strip()
        if answer and answer not in seen:
            seen.add(answer)
            answers.append(answer)
        if len(answers) >= 2:
            break
    if not answers:
        return None
    return "\n\n".join(answers)


def _make_feedback_meta(query, topic, location_context=None, waybill=None, rag_context=None):
    source_ref = _build_source_ref(topic, location_context, rag_context, waybill)
    return {
        "query": query,
        "topic": topic,
        "location_id": _extract_location_id(location_context),
        "waybill": waybill,
        "source_ref": source_ref,
    }


def _build_source_ref(topic, location_context=None, rag_context=None, waybill=None):
    """Sandbox — bot က Sheet/website ဘယ်ကနေ အဖြေယူခဲ့လဲ ရိုးရှင်းပြခြင်း。"""
    if waybill:
        return f"Website tracking | ဝေး {waybill}"

    loc_id = _extract_location_id(location_context) if location_context else None
    if loc_id:
        label = _extract_location_label(location_context)
        return f"Customer Inquire | #{loc_id} | {label or 'location row'}"

    if rag_context:
        loc_ids = re.findall(r"Location ID:\s*(\S+)", str(rag_context))
        if loc_ids:
            return f"Customer Inquire | #{loc_ids[0]} | AI ဖွင့်ပြ"
        if "status mean" in str(rag_context).lower() or "remark mean" in str(rag_context).lower():
            return "Customer Inquire 3 | Status/Remark | AI ဖွင့်ပြ"
        return "Sheet FAQ/Knowledge | AI ဖွင့်ပြ"

    if topic == "status_arrival":
        return "Website tracking | live API"
    return "AI — Sheet row တိုက်ရိုက်မမှီ"


def _build_ai_rating_markup(token):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👍 မှန်", callback_data=f"aifb:u:{token}"),
        types.InlineKeyboardButton("👎 မှား", callback_data=f"aifb:d:{token}"),
    )
    return markup


def _build_ai_reason_markup(token):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            AI_FEEDBACK_REASON_LABELS["data"],
            callback_data=f"aifb:r:{token}:data",
        ),
        types.InlineKeyboardButton(
            AI_FEEDBACK_REASON_LABELS["topic"],
            callback_data=f"aifb:r:{token}:topic",
        ),
        types.InlineKeyboardButton(
            AI_FEEDBACK_REASON_LABELS["tone"],
            callback_data=f"aifb:r:{token}:tone",
        ),
    )
    return markup


def register_ai_feedback_handlers(bot):
    """Sandbox-only inline rating for /ai replies."""

    @bot.callback_query_handler(func=lambda call: call.data.startswith("aifb:"))
    def handle_ai_feedback_callback(call):
        try:
            parts = call.data.split(":")
            if len(parts) < 3:
                bot.answer_callback_query(call.id, "မမှန်ကန်ပါ")
                return

            action, token = parts[1], parts[2]
            if call.message.chat.id != SANDBOX_CHAT_ID:
                bot.answer_callback_query(call.id, "Sandbox မှာသာ သုံးနိုင်ပါတယ်")
                return

            pending = db_manager.get_ai_feedback_pending(token)
            if not pending:
                bot.answer_callback_query(call.id, "အချိန်ကုန်သွားပါပြီ — ပြန် /ai မေးပါ")
                return
            if pending["completed"]:
                bot.answer_callback_query(call.id, "အဖြေကို မှတ်ပြီးပါပြီ")
                return
            if int(pending["user_id"]) != int(call.from_user.id):
                bot.answer_callback_query(call.id, "မိမိအဖြေကိုသာ မှတ်နိုင်ပါတယ်")
                return

            base_text = call.message.text or call.message.caption or ""

            if action == "d":
                bot.answer_callback_query(call.id, "ဘာကြောင့်မှားသလဲ ရွေးပေးပါ")
                prompt = "\n\n📝 **ဘာကြောင့်လဲ?** ရွေးပေးပါ အစ်ကို။"
                bot.edit_message_text(
                    base_text + prompt,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=_build_ai_reason_markup(token),
                    parse_mode="Markdown",
                )
                return

            if action == "u":
                ok, status = db_manager.save_ai_feedback_rating(token, call.from_user.id, "up")
                if not ok:
                    bot.answer_callback_query(call.id, "မှတ်မရပါ")
                    return
                bot.answer_callback_query(call.id, "👍 ကျေးဇူးတင်ပါတယ်")
                bot.edit_message_text(
                    base_text + "\n\n✅ _မှတ်ချက်: 👍 မှန်_",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                    parse_mode="Markdown",
                )
                return

            if action == "r" and len(parts) >= 4:
                reason = parts[3]
                ok, status = db_manager.save_ai_feedback_rating(
                    token, call.from_user.id, "down", reason=reason
                )
                if not ok:
                    bot.answer_callback_query(call.id, "မှတ်မရပါ")
                    return
                reason_label = AI_FEEDBACK_REASON_LABELS.get(reason, reason)
                bot.answer_callback_query(call.id, "မှတ်ပြီးပါပြီ")
                src = pending.get("source_ref") or "—"
                hint = (
                    f"\n\n✅ _မှတ်ချက်: 👎 {reason_label}_"
                    f"\n📎 **ကိုးကား:** `{src}`"
                    f"\n→ Sheet မှန်ရင် **bot** ပြင်ရန် | Sheet မမှန်ရင် **ထိုကိုးကား** ပြင်"
                )
                bot.edit_message_text(
                    base_text + hint,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                    parse_mode="Markdown",
                )
                return

            bot.answer_callback_query(call.id, "မမှန်ကန်ပါ")
        except Exception as e:
            log.error(f"❌ AI feedback callback error: {e}")
            try:
                bot.answer_callback_query(call.id, "အမှားရှိပါတယ်")
            except Exception:
                pass


def _get_message_thread_id(message):
    if getattr(message, "is_topic_message", False) and message.message_thread_id:
        return message.message_thread_id
    return None


def _reply_in_topic(bot, message, text, **kwargs):
    """Reply inside forum topic threads (OS group Pick Up / Inquire topics)."""
    thread_id = _get_message_thread_id(message)
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    try:
        return bot.reply_to(message, text, **kwargs)
    except Exception as e:
        if "parse" in str(e).lower() or "can't parse" in str(e).lower():
            kwargs.pop("parse_mode", None)
            return bot.reply_to(message, text, **kwargs)
        log.warning(f"⚠️ reply_to failed ({e}); retry send_message with thread_id={thread_id}")
        if thread_id:
            kwargs["message_thread_id"] = thread_id
            kwargs["reply_to_message_id"] = message.message_id
            return bot.send_message(message.chat.id, text, **kwargs)
        raise


def _send_ai_reply(bot, message, answer, feedback_meta=None):
    """Telegram reply with 4096 char split. Sandbox: attach 👍👎 rating."""
    full_response = f"🤖 **CarryMan AI Agent**\n\n{answer}"
    rating_markup = None
    if message.chat.id == SANDBOX_CHAT_ID and feedback_meta:
        source_ref = feedback_meta.get("source_ref") or ""
        token = db_manager.create_ai_feedback_pending(
            message.from_user.id,
            message.chat.id,
            feedback_meta.get("query"),
            answer,
            topic=feedback_meta.get("topic"),
            location_id=feedback_meta.get("location_id"),
            source_ref=source_ref,
        )
        rating_markup = _build_ai_rating_markup(token)
        full_response += f"\n\n📎 **ကိုးကား:** `{source_ref}`"
        full_response += "\n\n⭐ _Sandbox — အဖြေကို မှန်း ပေးပါ_"

    if len(full_response) > 4000:
        parts = [full_response[i:i + 4000] for i in range(0, len(full_response), 4000)]
        for idx, part in enumerate(parts):
            is_last = idx == len(parts) - 1
            markup = rating_markup if is_last else None
            if idx == 0:
                _reply_in_topic(bot, message, part, reply_markup=markup, parse_mode="Markdown")
            else:
                send_kwargs = {"reply_markup": markup, "parse_mode": "Markdown"}
                thread_id = _get_message_thread_id(message)
                if thread_id:
                    send_kwargs["message_thread_id"] = thread_id
                bot.send_message(message.chat.id, part, **send_kwargs)
    else:
        _reply_in_topic(bot, message, full_response, reply_markup=rating_markup, parse_mode="Markdown")


def handle_ai_query(bot, message, is_automatic=False):
    """
    Smart AI Support Logic (DB -> Maps -> AI)
    """
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        is_private = chat_id > 0
        is_sandbox = (chat_id == SANDBOX_CHAT_ID)

        if not _is_ai_globally_enabled(chat_id):
            log.info(f"🔇 /ai blocked — global_ai_answer is OFF (chat {chat_id})")
            _reply_in_topic(
                bot,
                message,
                "🔇 AI Support ကို ယာယီပိတ်ထားပါတယ်ခင်ဗျာ။ Admin က `/aion` ဖြင့် ပြန်ဖွင့်ပေးနိုင်ပါတယ်။",
            )
            return

        # 🛑 Group Chat Restriction: AI Auto-Answer is DISABLED in Groups for automatic replies.
        # Manual /ai queries are allowed.
        if not is_private and not is_sandbox and is_automatic:
            log.info(f"🔇 AI Auto-Answer is disabled in Group Chat {chat_id} for automatic replies. Returning silently.")
            return

        # ၁။ User Level သတ်မှတ်ခြင်း
        user_level = db_manager.get_user_level(user_id, chat_id)

        # --- Private Chat Logic (Staff Exemption & Three-Strike Rule) ---
        if is_private:
            # Check if human intervention is already needed
            _, human_needed = db_manager.get_user_state(user_id)
            if human_needed:
                log.info(f"🔇 AI Muted for user {user_id} due to human_intervention_needed")
                bot.reply_to(
                    message,
                    "⚠️ AI auto-reply ကို ယာယီရပ်ထားပါတယ်ခင်ဗျာ။ Admin ဆီက ဆက်သွယ်ပေးပါ (သို့) `/unmute` ဖြင့် ပြန်ဖွင့်နိုင်ပါတယ်။"
                )
                return

            # Staff Exemption (Level 3/4)
            if user_level >= 3:
                log.info(f"👑 Staff Exemption: Full AI access for user {user_id}")
                # Proceed to general AI response without RAG restrictions if needed,
                # but for now we'll just let them pass the out-of-scope check.
            else:
                # Non-Staff: Apply Strict RAG & Three-Strike Rule
                pass # Will be handled during intent/scope check

        # ၂။ မေးခွန်းကို ရယူခြင်း
        query = _normalize_ai_query(message.text) if not is_automatic else _normalize_ai_query(message.text or message.caption or '')
        if not query and message.reply_to_message:
            query = _normalize_ai_query(message.reply_to_message.text or message.reply_to_message.caption)
            
        if not query:
            _reply_in_topic(
                bot,
                message,
                "💡 **ဘယ်လိုကူညီပေးရမလဲခင်ဗျာ?**\n\nမေးခွန်းကို တွဲရိုက်ပါ (သို့) မေးလိုသောစာကို Reply ပြန်ပြီး `/ai` လို့ ရိုက်ပါ။",
            )
            return

        log.info(f"🤖 AI Query from {user_id}: {query[:50]}...")

        search_query, is_correction, needs_clarification = _resolve_ai_search_query(
            user_id, chat_id, query, user_level
        )
        if search_query != query:
            log.info(f"🔍 AI search query expanded: {search_query[:80]}...")

        if needs_clarification:
            clarify = (
                "နောက်ဆုံး မေးခွန်းကို နားလည်ဖို့ နေရာနာမည် (သို့) ဝေးနံပါတ် "
                "ထပ်ပြောပေးပါနော်ခင်ဗျ။"
            )
            if is_automatic:
                log.info(f"🔇 Auto AI skipped — ambiguous follow-up without context (user {user_id})")
                return
            _reply_in_topic(bot, message, clarify)
            return

        if is_private and user_level < 3:
            if not _is_allowed_private_customer_query(search_query, user_level):
                log.info(f"🛡️ Private /ai scope gate blocked user {user_id}: {query[:80]}")
                _handle_private_scope_violation(bot, message, user_id)
                return

        ai_topic = _classify_ai_topic(search_query)
        log.info(f"🎯 AI Topic: {ai_topic}")

        if ai_topic == "status_arrival":
            from modules import check_order
            waybill = check_order.extract_waybill(search_query)

            if is_private and user_level < 3 and not waybill:
                bot.reply_to(
                    message,
                    "အခြေအနေလေးပြောပြပေးနိုင်ရန် Way Bill လေးပို့ပေးပါနော်"
                )
                db_manager.update_message_status(message.message_id, chat_id, 'HANDLED_BY_AI')
                return

            _reply_in_topic(bot, message, "⏳ website tracking ထဲကနေ စစ်ဆေးနေပါတယ်ခင်ဗျ...")
            success, tracking_answer = check_order.query_live_tracking(chat_id, search_query)
            if not success:
                tracking_answer = (
                    "တောင်းပန်ပါတယ်ခင်ဗျ။ live tracking စစ်ဆေးမရသေးပါ။ "
                    "နောက်မှ `/ai` နဲ့ ပြန်မေးပေးပါ (သို့) Admin ကို ဆက်သွယ်ပေးပါ။"
                )
            _send_ai_reply(
                bot, message, tracking_answer,
                _make_feedback_meta(
                    query, "status_arrival",
                    waybill=check_order.extract_waybill(search_query),
                ),
            )
            _record_ai_turn(
                user_id, chat_id, query, tracking_answer, "status_arrival",
                waybill=check_order.extract_waybill(search_query),
            )
            db_manager.update_message_status(message.message_id, chat_id, 'HANDLED_BY_AI')
            return

        # 📍 Location/Delivery table (full fee row) — direct reply when Sheet data is complete
        location_context = db_manager.search_location_delivery(search_query, user_level)
        direct_location_answer = db_manager.format_location_delivery_reply(location_context)
        if direct_location_answer:
            if is_correction:
                direct_location_answer = (
                    "ဟုတ်ကဲ့ ခင်ဗျ၊ ပြန်စစ်ပေးလိုက်ပါတယ်။\n"
                    + direct_location_answer
                )
            log.info("📍 Direct location delivery reply from synced Sheet data.")
            _send_ai_reply(
                bot, message, direct_location_answer,
                _make_feedback_meta(query, "delivery_info", location_context=location_context),
            )
            _record_ai_turn(
                user_id, chat_id, query, direct_location_answer, "delivery_info",
                location_context=location_context,
            )
            db_manager.update_message_status(message.message_id, chat_id, 'HANDLED_BY_AI')
            return

        # ၃။ Google Sheet → DB synced knowledge (pre-fetch for all AI providers)
        is_staff = user_level >= 3
        conversation_turns = db_manager.get_ai_conversation_turns(user_id, chat_id)
        conversation_block = _build_conversation_block(conversation_turns)
        rag_context = db_manager.search_delivery_knowledge(search_query, user_level)
        if location_context:
            rag_context = (location_context + "\n---\n" + rag_context) if rag_context else location_context
        if rag_context:
            rag_block = f"""
[Retrieved Knowledge Base Data (synced from Google Sheet)]:
{rag_context}
CRITICAL: Use the retrieved data above as your PRIMARY source for fees, locations, COD, and delivery rules.
Do NOT invent numbers or locations that are not supported by this data or Core Policies.
"""
            log.info(f"📚 RAG pre-fetch: matched knowledge rows for query ({len(rag_context)} chars)")
        else:
            rag_block = """
[Retrieved Knowledge Base Data]: No direct match found for this query in the synced database.
Use Core Policies and Base Company Info only. If still insufficient, reply with the standard 'don't know' message for Level 1 users.
"""
            log.info("📚 RAG pre-fetch: no knowledge rows matched")

        scope_check_prompt = ""
        if is_private and not is_staff:
            scope_check_prompt = """
            Scope Check: 'Determine if the user query is related to CarryMan Logistics services (delivery, tracking, pickup, pricing, locations).
            If it is OUT OF SCOPE (e.g., coding, math, general knowledge, personal questions), output ONLY the word "OUT_OF_SCOPE".
            Otherwise, proceed with the answer.'
            """

        rag_instructions = ai_utils.get_rag_instructions(user_level)
        topic_instructions = ai_utils.get_topic_ai_instructions(ai_topic)
        tone_block = db_manager.load_tone_examples(user_level)

        # Permanent Base Context (Company Info)
        base_company_info = """
        [Base Company Info]:
        - Office Address: အမှတ်(၁)၊ ဇေယျသုခလမ်း၊ နှင်းဆီကုန်းဘူတာအနီး၊ သင်္ဃန်းကျွန်းမြို့နယ်၊ ရန်ကုန်မြို့။
        - Office Hours: နေ့စဉ် မနက် ၉ နာရီမှ ညနေ ၆ နာရီအထိ (အခါကြီးရက်ကြီးများသာ ပိတ်ပါသည်)။
        - Contact Numbers: 09789102234, 09899065899
        - Google Maps: https://maps.app.goo.gl/CarryManRealLocation (အစ်ကို့ရဲ့ တကယ့် Link ကို ဒီမှာ အစားထိုးနိုင်ပါတယ်)
        """

        # Fetch Core Policies Dynamically from Database
        core_policies = db_manager.get_core_policies()

        ai_prompt = f"""
        Strict Persona & Tone: 'You are an Online Shop (OS) admin. You MUST strictly follow the tone, style, and examples provided in the OS Tone_&_Example data. Keep answers short, direct, and natural. NEVER use generic AI fluff.'

        {rag_instructions}

        {topic_instructions}

        {tone_block}

        {scope_check_prompt}

        {base_company_info}

        {core_policies}

        {conversation_block}

        {rag_block}

        RULE: You must apply LOGICAL REASONING using the Base Context (including the Dynamic Core Policies), Retrieved Knowledge Base Data, and any additional tool results.
        If a user asks about an item (e.g., plates, guns, glass, liquid), evaluate it against the provided Terms and Conditions instead of looking for exact word matches.
        - Example: If asked about 'plates' (ပန်းကန်), reason that it is a 'Fragile Item' and apply the fragile item rule found in the policies.
        - Example: If asked about 'Kyaikto' (ကျိုက်ထို), use the Retrieved Knowledge Base Data first; if still incomplete, use the 'search_database' tool.

        CRITICAL: Prefer Retrieved Knowledge Base Data first. Use 'search_database' only if you need additional lookup.

        Comprehensive Data Extraction: 'When a user asks about delivery to a specific location, extract from Retrieved Knowledge Base Data:
        - Whether Home Delivery is available.
        - The Delivery Fee (Base weight and extra charge).
        - Whether COD (Cash on Delivery) is accepted.
        - Estimated Delivery Duration (Days).'

        Location Labeling: 'Always clearly state the Township and City in your response.'

        Format Constraint: 'Combine these details into a single, concise, human-like paragraph in Burmese.'

        User Query: "{search_query}"
        """
        
        # Get tools based on user level (Binary Access Control)
        tools = ai_utils.get_ai_tools(user_level)
        
        # Initial AI Call
        response = ai_utils.get_ai_completion(ai_prompt, timeout=45.0, tools=tools, user_level=user_level)
        
        if not response:
            fallback_answer = _format_rag_fallback_answer(rag_context)
            if fallback_answer:
                log.warning("⚠️ AI API unavailable — replying from synced Sheet data (RAG fallback).")
                _send_ai_reply(
                    bot, message, fallback_answer,
                    _make_feedback_meta(
                        query, ai_topic, location_context=location_context, rag_context=rag_context,
                    ),
                )
                _record_ai_turn(user_id, chat_id, query, fallback_answer, ai_topic)
                db_manager.update_message_status(message.message_id, chat_id, 'HANDLED_BY_AI')
                return
            _reply_in_topic(bot, message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")
            return

        # Handle Tool Calls (if any)
        # Note: OpenRouter/OpenAI response might contain tool_calls
        # For simplicity in this implementation, we'll handle a single turn of tool calling
        # In a production environment, you might want a loop for multiple tool calls.
        
        # We need to check if the response is a tool call or a direct answer.
        # Since get_ai_completion returns content string, we might need to adjust it
        # to return the full response object if we want to handle tool calls properly.
        # However, for now, let's assume get_ai_completion handles the tool execution
        # or we modify it to handle the logic.
        
        # Let's refine get_ai_completion in ai_utils.py to handle tool execution internally
        # to keep main_router clean.
        answer = response

        # --- Three-Strike Rule Implementation (AI secondary scope check) ---
        if is_private and not is_staff and answer and "OUT_OF_SCOPE" in answer:
            log.info(f"🛡️ AI OUT_OF_SCOPE flagged for private user {user_id}")
            _handle_private_scope_violation(bot, message, user_id)
            return
        if not answer:
            _reply_in_topic(bot, message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")
            return

        answer = ai_utils.format_human_like_answer(answer, tone_block=tone_block)
        _send_ai_reply(
            bot, message, answer,
            _make_feedback_meta(
                query, ai_topic, location_context=location_context, rag_context=rag_context,
            ),
        )
        _record_ai_turn(user_id, chat_id, query, answer, ai_topic)
            
        # Mark as Handled by AI to suppress escalations
        db_manager.update_message_status(message.message_id, chat_id, 'HANDLED_BY_AI')

    except Exception as e:
        log.error(f"❌ AI Query Error: {e}")
        _reply_in_topic(bot, message, "⚠️ တောင်းပန်ပါတယ်ခင်ဗျာ။ အဖြေရှာနေစဉ် အမှားတစ်ခု ဖြစ်သွားလို့ပါ။")

def route_message(bot, message):
    """
    AI မှ Message ကို ဖတ်ပြီး သက်ဆိုင်ရာ Module ဆီသို့ လမ်းကြောင်းပြောင်းပေးခြင်း
    Returns: True if a module handled the message (caller should skip duplicate log_message),
             False/None otherwise (caller should proceed with normal log_message).
    """
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        text = message.text or message.caption

        # Manual /ai — always allow in OS groups (incl. staff testing in sandbox)
        stripped = (text or "").strip()
        if stripped.startswith("/ai") or (stripped.split()[:1] == ["/ai"]):
            if _is_ai_globally_enabled(chat_id):
                handle_ai_query(bot, message, is_automatic=False)
            else:
                log.info("🔇 AI Command (/ai) ignored because global_ai_answer is OFF")
                _reply_in_topic(
                    bot,
                    message,
                    "🔇 AI Support ကို ယာယီပိတ်ထားပါတယ်ခင်ဗျာ။ Admin က `/aion` ဖြင့် ပြန်ဖွင့်ပေးနိုင်ပါတယ်။",
                )
            return True

        # 🛑 Staff auto-route exclusion (manual /ai handled above)
        user_level = db_manager.get_user_level(user_id, chat_id)
        is_staff = user_level >= 3
        is_private = chat_id > 0

        if not is_private and is_staff:
            log.info(f"🛡️ Staff Exclusion (Group): Skipping AI routing for staff {user_id}")
            return False

        if not text:
            return False

        if getattr(message.from_user, 'is_bot', False):
            return False

        if text.strip().startswith('⏳ Auto Pickup') or 'Auto Pickup အချက်အလက်များ' in text[:120]:
            log.info(f"🛡️ Skipping router: bot status card detected (msg {message.message_id})")
            return False

        global_ai = db_manager.get_ai_global_status()
        is_sandbox = (chat_id == SANDBOX_CHAT_ID)
        if text.startswith('/'):
            return False

        group_ai = db_manager.get_group_ai_status(chat_id)
        global_pickup = db_manager.get_auto_pickup_global_status()
        global_alert = db_manager.get_alert_system_global_status()
        
        if is_sandbox:
            log.info(f"🧪 Sandbox Mode: Bypassing all global/group/time restrictions for chat {chat_id}")
            # Force all toggles to ON for sandbox
            global_ai = 'ON'
            group_ai = 'ON'
            global_pickup = 'ON'
        
        if _looks_like_delivery_support_question(text) or _looks_like_status_question(text):
            auto_delivery = db_manager.get_ai_auto_delivery_status() == 'ON'
            if (
                is_private
                and not is_staff
                and auto_delivery
                and _is_ai_globally_enabled(chat_id)
                and global_ai == 'ON'
            ):
                log.info(f"🤖 Auto delivery reply triggered (private): {text[:50]}...")
                handle_ai_query(bot, message, is_automatic=True)
                return True
            log.info(f"🔇 Delivery/support question — silent unless /ai: {text[:50]}...")
            return False

        log.info(f"Routing message from {chat_id}: {text[:50]}...")

        # ၂။ AI Decision (Intent Detection)
        # လက်ရှိ modules folder ထဲမှာ ရှိတဲ့ module list ကို ယူမယ်
        # 💡 support module ကို automatic routing မှ ဖယ်ထုတ်ထားပါသည် (Manual /ai သာ သုံးမည်)
        available_modules = ["auto_pickup", "auditor"]
        
        prompt = f"""
        Role: Central AI Router for a Logistics Bot.
        Task: Analyze the user message and decide which module should handle it.
        
        Available Modules:
        - auto_pickup: Use for NEW pickup requests OR messages that contain structured pickup/order information.
          THIS INCLUDES:
          1. Explicit pickup requests: "pick up လာယူပေးပါ", "လာကောက်ပေးပါ", "မနက်ဖြန်အတွက် တင်ပေးပါ", "တင်ပေးပါ", "ခေါ်ပေးပါ", "pick up ရဦးမလား", "ဒီနေ့ pickup ရှိလား"
          2. Structured order lists with OS Name/Date/Parcel info: e.g., "OS Name - ShopX\\nDate -16/5/2026\\nစုစုပေါင်း ပါဆယ် - (5)ထုတ်", "16.5.2026။ 24ထုတ်"
          3. Messages indicating order submission: "တင်ထားပါတယ်", "တင်ပေးလိုက်ပါတယ်", "တင်လိုက်ပါတယ်"
          4. Inquiries about pickup availability or timing
          DO NOT output 'none' for any of the above -- they ARE pickup requests even if they look like lists.
        - check_order: Use for checking order status, tracking numbers, or finding specific orders.
        - auditor: Use for complaints, questions about past/delayed pickups, or when the user is asking about an ALREADY PLACED pickup (e.g., "pick up မလာသေးဘူးလား", "ဘယ်အချိန်လာမှာလဲ").
        - none: Use for delivery/pricing/location/COD questions, greetings, casual chit-chat, spam, or general logistics questions that are NOT pickup requests. Examples: "မြဝတီကပြန်ရလားပို့လို့", "ပို့ခဘယ်လောက်လဲ", "COD ရလား".

        User Message: "{text}"

        Output Rules:
        1. Output ONLY the module name in lowercase.
        2. Structured order data (OS Name + Date format) -> output 'auto_pickup'.
        3. "တင်ထားပါတယ်" or similar submission confirmations -> output 'auto_pickup'.
        4. Delivery/pricing/location/COD/office-info questions without /ai -> output 'none'.
        5. Casual chit-chat or greetings -> output 'none'.
        6. If truly unsure about pickup vs none, choose 'auto_pickup' ONLY when pickup intent is plausible; otherwise choose 'none'.
        """

        intent = ai_utils.get_ai_completion(prompt, timeout=30.0)
        if not intent:
            log.error("❌ Both OpenRouter and Gemini Fallback failed.")
            return False
        intent = intent.strip().lower()
        log.info(f"🎯 AI Decision: {intent}")

        if intent == "none":
            return False

        if intent == "check_order":
            log.info("🔇 check_order intent ignored — use manual /ai for live tracking.")
            return False

        # 🛑 Group Chat Restriction: ONLY allow auto_pickup and auditor.
        # Block support, check_order, and any other general AI routing in Groups.
        if not is_private and not is_sandbox:
            allowed_group_intents = ["auto_pickup", "auditor"]
            if intent not in allowed_group_intents:
                log.info(f"🔇 Blocking general intent '{intent}' in Group Chat {chat_id}. Bot will remain silent.")
                return False

        # ၃။ Gatekeeper Logic (Phase 2)
        # Auto Pickup: ၂၄ နာရီ (Global ON ဖြစ်ရမည်)
        # AI Answer (Support/Auditor): 09:00 AM - 08:00 PM (Global & Group ON ဖြစ်ရမည်)
        
        # --- Private Chat Audit ---
        is_private = chat_id > 0
        user_level = db_manager.get_user_level(user_id, chat_id)
        is_staff = user_level >= 3

        if intent == "auto_pickup":
            # Rule: Pickup works ONLY in Group Chats for Non-Staff users.
            if is_private:
                log.info(f"⏭️ Skipping Auto Pickup: Private Chat detected. Pickup is Group-only.")
                return False
            if is_staff:
                log.info(f"🛡️ Staff Safety Net: Blocking auto_pickup routing for staff {user_id}")
                return False
            # Note: Notification is sent regardless of global_pickup status.
            # Silent mode for groups (when OFF) is handled inside the module.
        elif intent == "auditor":
            if is_private and not is_staff:
                log.info(f"⏭️ Skipping Auditor: Private Chat detected for non-staff")
                return False
            if not is_sandbox:
                if not is_private and global_alert != 'ON':
                    log.info("⏭️ Skipping Auditor: Alert System is OFF")
                    return False
        else:
            # Support (AI Answer) အတွက် စစ်ဆေးခြင်း
            if not is_sandbox:
                if global_ai != 'ON':
                    log.info(f"⏭️ Skipping AI Answer: Global Status is {global_ai}")
                    return False
                # Private chat doesn't have group_ai setting, so we skip that check for private
                if not is_private and group_ai != 'ON':
                    log.info(f"⏭️ Skipping AI Answer: Group Status is {group_ai}")
                    return False
                if not is_ai_office_hours():
                    log.info("🌙 Skipping AI Answer: Outside Office Hours (09:00 AM - 08:00 PM)")
                    return False

        # ၄။ Dynamic Loader (importlib)
        if intent in available_modules:
            try:
                # modules.intent ပုံစံဖြင့် import လုပ်မည်
                module_path = f"modules.{intent}"
                module = importlib.import_module(module_path)
                
                # Module တိုင်းမှာ handle(bot, message) function ရှိရမည်
                if hasattr(module, 'handle'):
                    module.handle(bot, message)
                else:
                    log.warning(f"⚠️ Module {intent} has no 'handle' function.")
            except ImportError as ie:
                log.error(f"❌ Could not import module {intent}: {ie}")
            except Exception as me:
                log.error(f"❌ Error executing module {intent}: {me}")
            return True
        else:
            log.warning(f"⚠️ AI suggested unknown module: {intent}")
        return False

    except Exception as e:
        log.error(f"❌ Router Error: {e}")
