import os
import asyncio
from telethon import TelegramClient
from telethon.tl import functions, types
from dotenv import load_dotenv
import db_manager

load_dotenv()
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_USERNAME = os.getenv('BOT_USERNAME')

STAFF_USERNAMES = ['@cmsod1', '@cmmarketing1', '@cmfinance1', '@dataentrycm1']

async def toggle_forum_safe(client, channel):
    ReqClass = functions.channels.ToggleForumRequest
    try: return await client(ReqClass(channel=channel, enabled=True, tabs=True))
    except: pass
    try: return await client(ReqClass(channel, True, True))
    except: pass
    try: return await client(ReqClass(channel=channel, enabled=True))
    except: pass
    try: return await client(ReqClass(channel, True))
    except: pass

async def rename_general_safe(client, channel, title):
    ReqClass = getattr(functions.channels, 'EditForumTopicRequest', None) or getattr(functions.messages, 'EditForumTopicRequest', None)
    if not ReqClass: return False
    try: await client(ReqClass(channel=channel, topic_id=1, title=title)); return True
    except: pass
    try: await client(ReqClass(channel, 1, title)); return True
    except: pass
    try: await client(ReqClass(peer=channel, topic_id=1, title=title)); return True
    except: pass
    try: await client(ReqClass(channel, 1, title=title)); return True
    except: return False

async def create_topic_safe(client, channel, title):
    ReqClass = getattr(functions.channels, 'CreateForumTopicRequest', None) or getattr(functions.messages, 'CreateForumTopicRequest', None)
    if not ReqClass: raise Exception("Library issue")
    try: return await client(ReqClass(channel=channel, title=title))
    except: pass
    try: return await client(ReqClass(channel, title))
    except: pass
    try: return await client(ReqClass(peer=channel, title=title))
    except: pass
    try: return await client(ReqClass(channel, title=title))
    except Exception as e: raise Exception(f"Failed to create topic: {e}")

# 💡 Rank ကို ဖြုတ်လိုက်ပါပြီ
async def create_group_task(group_name):
    async with TelegramClient('carryman', int(API_ID), API_HASH) as client:
        print(f"🚀 Creating Group: {group_name}")
        
        created_chat = await client(functions.channels.CreateChannelRequest(
            title=group_name, about="CarryMan Official OS Group", megagroup=True
        ))
        channel = created_chat.chats[0]
        
        try:
            invite_result = await client(functions.messages.ExportChatInviteRequest(peer=channel))
            invite_link = invite_result.link
        except: 
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
        except: pass

        general_topic = "Pick Up/Urgent/စုံစမ်းရန်"
        general_msg = "🎧 Pick Up ခေါ်ခြင်း၊ ပါဆယ်အခြေအနေစုံစမ်းခြင်းနှင့် အရေးကြီးပို့ပေးရမည့်ဝေးများကို ဒီမှာပြောနိုင်ပါတယ်နော်။"
        
        is_renamed = await rename_general_safe(client, channel, general_topic)
        topic_name_1 = general_topic if is_renamed else "General"
        await client.send_message(channel, general_msg, reply_to=1)
        
        # 💡 Data (၅) မျိုးသာ သိမ်းမည်
        db_records = [(group_name, channel.id, invite_link, topic_name_1, 1)]

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
                db_records.append((group_name, channel.id, invite_link, t_name, topic_id))
            except: pass

        for user in STAFF_USERNAMES:
            try: await client(functions.channels.InviteToChannelRequest(channel=channel, users=[user]))
            except: pass
            
        try:
            await client(functions.channels.InviteToChannelRequest(channel=channel, users=[BOT_USERNAME]))
            await client(functions.channels.EditAdminRequest(
                channel=channel, user_id=BOT_USERNAME, 
                admin_rights=types.ChatAdminRights(post_messages=True, delete_messages=True, invite_users=True, pin_messages=True, manage_topics=True),
                rank='AI Assistant'
            ))
        except: pass
        
        return db_records, invite_link

def create_new_group(bot, message):
    # 💡 စာသားပုံစံ အသစ် (/newgroup Group Name)
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
            # 💡 rank ကို ဖြုတ်၍ ကော်လံ ၅ ခုသာ Insert လုပ်မည်
            c.execute("INSERT INTO os_groups (group_name, group_id, invite_link, topic_name, topic_id) VALUES (?, ?, ?, ?, ?)", record)
        conn.commit()
        conn.close()
        
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                              text=f"✅ **{group_name}** ကို အောင်မြင်စွာ ဖန်တီးပြီး Database သို့ သိမ်းဆည်းပြီးပါပြီ။\n🔗 {invite_link}")
    except Exception as e:
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"❌ Error: {e}")
