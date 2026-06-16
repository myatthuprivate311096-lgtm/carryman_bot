import os
import asyncio
import sys
from telethon import TelegramClient
from telethon.tl import functions, types
from dotenv import load_dotenv

# 💡 Absolute Path Fix for Module
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import db_manager
from logger import log
from telebot import util

load_dotenv(os.path.join(BASE_DIR, '.env'))
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_USERNAME = os.getenv('BOT_USERNAME')

# အရင်က သုံးခဲ့တဲ့ staff @username များ (အလုပ်လုပ်နေခဲ့)
STAFF_USERNAMES = ['@cmsod1', '@cmmarketing1', '@cmfinance1', '@dataentrycm1', 8548232517]


class GroupCreatorAuthError(Exception):
    """Telethon session auth invalid/expired for newgroup flow."""


def telethon_channel_to_chat_id(channel):
    """Telethon channel.id → Bot API supergroup chat_id (-100...)."""
    cid = channel.id
    if cid and cid > 0:
        return int(f"-100{cid}")
    return cid


def _staff_add_targets_for_new_group():
    """အရင်က STAFF_USERNAMES + staff DB (group ဖွင့်တိုင်း တိုက်ရိုက် add)."""
    targets = []
    seen = set()
    for ref in STAFF_USERNAMES:
        key = ref if isinstance(ref, str) else ref
        if key not in seen:
            targets.append(ref)
            seen.add(key)
    for row in db_manager.get_all_staff():
        uid = int(row[0])
        if uid not in seen:
            targets.append(uid)
            seen.add(uid)
    return targets


async def toggle_forum_safe(client, channel):
    ReqClass = functions.channels.ToggleForumRequest
    try: return await client(ReqClass(channel=channel, enabled=True, tabs=True))
    except Exception as e: log.debug(f"ToggleForum attempt 1 failed: {e}")
    try: return await client(ReqClass(channel, True, True))
    except Exception as e: log.debug(f"ToggleForum attempt 2 failed: {e}")
    try: return await client(ReqClass(channel=channel, enabled=True))
    except Exception as e: log.debug(f"ToggleForum attempt 3 failed: {e}")
    try: return await client(ReqClass(channel, True))
    except Exception as e: log.error(f"❌ All ToggleForum attempts failed: {e}")

async def rename_general_safe(client, channel, title):
    ReqClass = getattr(functions.channels, 'EditForumTopicRequest', None) or getattr(functions.messages, 'EditForumTopicRequest', None)
    if not ReqClass:
        log.error("❌ EditForumTopicRequest not found in library")
        return False
    try: await client(ReqClass(channel=channel, topic_id=1, title=title)); return True
    except Exception as e: log.debug(f"RenameGeneral attempt 1 failed: {e}")
    try: await client(ReqClass(channel, 1, title)); return True
    except Exception as e: log.debug(f"RenameGeneral attempt 2 failed: {e}")
    try: await client(ReqClass(peer=channel, topic_id=1, title=title)); return True
    except Exception as e: log.debug(f"RenameGeneral attempt 3 failed: {e}")
    try: await client(ReqClass(channel, 1, title=title)); return True
    except Exception as e:
        log.error(f"❌ All RenameGeneral attempts failed: {e}")
        return False

async def create_topic_safe(client, channel, title):
    ReqClass = getattr(functions.channels, 'CreateForumTopicRequest', None) or getattr(functions.messages, 'CreateForumTopicRequest', None)
    if not ReqClass:
        log.error("❌ CreateForumTopicRequest not found in library")
        raise Exception("Library issue")
    try: return await client(ReqClass(channel=channel, title=title))
    except Exception as e: log.debug(f"CreateTopic attempt 1 failed: {e}")
    try: return await client(ReqClass(channel, title))
    except Exception as e: log.debug(f"CreateTopic attempt 2 failed: {e}")
    try: return await client(ReqClass(peer=channel, title=title))
    except Exception as e: log.debug(f"CreateTopic attempt 3 failed: {e}")
    try: return await client(ReqClass(channel, title=title))
    except Exception as e:
        log.error(f"❌ All CreateTopic attempts failed: {e}")
        raise Exception(f"Failed to create topic: {e}")

async def _connect_authorized_client():
    """Connect without auto-start() to avoid EOF from interactive phone prompt."""
    session_path = os.path.join(BASE_DIR, 'carryman')
    client = TelegramClient(session_path, int(API_ID), API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise GroupCreatorAuthError(
            "Telethon session is not authorized. "
            "Run telethon_login.py on VPS to refresh carryman.session."
        )
    return client


async def create_group_task(group_name):
    client = await _connect_authorized_client()
    try:
        print(f"🚀 Creating Group: {group_name}")

        created_chat = await client(functions.channels.CreateChannelRequest(
            title=group_name, about="CarryMan Official OS Group", megagroup=True
        ))
        channel = created_chat.chats[0]

        try:
            invite_result = await client(functions.messages.ExportChatInviteRequest(peer=channel))
            invite_link = invite_result.link
        except Exception:
            invite_link = "Link Error"

        await toggle_forum_safe(client, channel)
        await asyncio.sleep(3)

        try:
            await client(functions.messages.EditChatDefaultBannedRightsRequest(
                peer=channel,
                banned_rights=types.ChatBannedRights(
                    until_date=None, send_messages=False, send_media=False, send_stickers=False, send_gifs=False,
                    send_games=False, send_inline=False, embed_links=False, send_polls=False, invite_users=False,
                    change_info=False, pin_messages=False, manage_topics=False
                )
            ))
        except Exception as e:
            log.warning(f"⚠️ Failed to set default banned rights: {e}")

        general_topic = "Pick Up/Urgent/စုံစမ်းရန်"
        general_msg = "🎧 Pick Up ခေါ်ခြင်း၊ ပါဆယ်အခြေအနေစုံစမ်းခြင်းနှင့် အရေးကြီးပို့ပေးရမည့်ဝေးများကို ဒီမှာပြောနိုင်ပါတယ်နော်။"

        is_renamed = await rename_general_safe(client, channel, general_topic)
        topic_name_1 = general_topic if is_renamed else "General"
        await client.send_message(channel, general_msg, reply_to=1)

        chat_id = telethon_channel_to_chat_id(channel)
        db_records = [(group_name, chat_id, invite_link, topic_name_1, 1)]

        other_topics = {
            "Error": "ပို့မရပါဆယ်များကို အကြောင်းအရာနှင့်တစ်ကွ ဒီမှာအကြောင်းကြားပေးသွားပါ့ရှင့်။ Reply ဆွဲ၍ အကြောင်းလေးပြန်ပေးပါနော်။",
            "Fin & Voc": "ငွေစာရင်းပို့ပေးခြင်းနှင့် ဘောင်ချာများကို ဒီမှာပို့ပေးသွားပါ့မယ်နော်။"
        }

        for t_name, t_msg in other_topics.items():
            try:
                topic_result = await create_topic_safe(client, channel, t_name)
                topic_id = 1
                if hasattr(topic_result, 'updates'):
                    for update in topic_result.updates:
                        if hasattr(update, 'id'):
                            topic_id = update.id
                            break
                await client.send_message(channel, t_msg, reply_to=topic_id)
                db_records.append((group_name, chat_id, invite_link, t_name, topic_id))
            except Exception as e:
                log.error(f"❌ Failed to create topic {t_name}: {e}")

        # Staff အားလုံး group ထဲ တိုက်ရိုက် add (အရင်က loop အတိုင်း + staff DB)
        for user in _staff_add_targets_for_new_group():
            try:
                await client(functions.channels.InviteToChannelRequest(channel=channel, users=[user]))
            except Exception as e:
                log.warning(f"⚠️ Failed to add staff {user}: {e}")

        try:
            await client(functions.channels.InviteToChannelRequest(channel=channel, users=[BOT_USERNAME]))
            await client(functions.channels.EditAdminRequest(
                channel=channel, user_id=BOT_USERNAME,
                admin_rights=types.ChatAdminRights(
                    post_messages=True, delete_messages=True, invite_users=True,
                    pin_messages=True, manage_topics=True,
                ),
                rank='AI Assistant'
            ))
        except Exception as e:
            log.error(f"❌ Failed to invite/promote bot: {e}")

        return db_records, invite_link
    finally:
        await client.disconnect()

def create_new_group(bot, message):
    group_name = message.text.replace('/newgroup ', '').strip()

    if not group_name or group_name == "/newgroup":
        bot.reply_to(message, "⚠️ ပုံစံမှားနေပါသည်။ ဥပမာ: `/newgroup Shop A`", parse_mode='Markdown')
        return

    msg = bot.reply_to(message, f"⏳ **{group_name}** အား ဖန်တီးနေပါသည်... (စက္ကန့်အနည်းငယ် စောင့်ပါ)")

    try:
        db_records, invite_link = asyncio.run(create_group_task(group_name))

        conn = db_manager.get_connection()
        c = conn.cursor()
        for record in db_records:
            t_name = record[3]
            target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
            target_topic = 1
            t_name_l = t_name.lower()

            if any(x in t_name_l for x in ["error", "ပို့မရ"]):
                target_topic = 37
            elif any(x in t_name_l for x in ["fin", "voc", "ငွေစာရင်း", "ဘောင်ချာ"]):
                target_topic = 35
            elif any(x in t_name_l for x in ["pick up", "urgent", "စုံစမ်းရန်"]):
                target_topic = 1

            full_record = (record[1], record[0], record[1], record[0], "Manual Register", record[3], record[4], target_chat, target_topic)
            c.execute(
                "INSERT INTO os_groups (chat_id, shop_name, group_id, group_name, invite_link, topic_name, topic_id, target_chat_id, target_topic_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                full_record,
            )
        conn.commit()
        conn.close()

        esc_name = util.escape(group_name)
        esc_link = util.escape(invite_link)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=f"✅ **{esc_name}** ကို အောင်မြင်စွာ ဖန်တီးပြီး Database သို့ သိမ်းဆည်းပြီးပါပြီ။\n🔗 {esc_link}",
            parse_mode="Markdown",
        )
    except (GroupCreatorAuthError, EOFError) as e:
        log.error(f"❌ /newgroup auth/session error: {e}")
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=(
                "❌ Telegram session (`carryman.session`) expired.\n\n"
                "VPS SSH မှာ run ပေးပါ:\n"
                "`python3 telethon_login.py`\n\n"
                "Phone + OTP ထည့်ပြီးရင် `/newgroup` ပြန်စမ်းပါ။"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        err_text = str(e).lower()
        if any(x in err_text for x in ("auth", "session", "unauthorized", "sign in", "login required")):
            log.error(f"❌ /newgroup auth-like failure: {e}")
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text=(
                    "❌ Telethon auth issue detected.\n"
                    "VPS မှာ `python3 telethon_login.py` run ပြီး session refresh လုပ်ပါ။"
                ),
                parse_mode="Markdown",
            )
            return
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"❌ Error: {util.escape(str(e))}", parse_mode="Markdown")
