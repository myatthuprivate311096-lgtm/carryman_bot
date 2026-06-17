import telebot
from logger import log
import db_manager
import main_router
def register_message_handlers(bot: telebot.TeleBot, is_manager_func):
    """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း နှင့် Routing လုပ်ခြင်း """

    @bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'voice', 'video', 'document', 'video_note', 'audio'])
    def handle_all_messages(message):
        """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း """
        try:
            # 🧠 Central AI Router (Sandbox Logic Inside)
            # Returns True if a module handled the message (skip duplicate log_message)
            handled_by_module = main_router.route_message(bot, message)

            chat_id = message.chat.id
            user_id = message.from_user.id
            
            # 💡 Media Type အလိုက် စာသားပြောင်းလဲခြင်း
            media_id = None
            text = message.text or message.caption
            
            if not text:
                if message.photo:
                    text = "🖼️ Photo"
                    media_id = message.photo[-1].file_id
                elif message.voice:
                    text = "🎙️ Voice Message"
                    media_id = message.voice.file_id
                elif message.video:
                    text = "📹 Video"
                    media_id = message.video.file_id
                elif message.video_note:
                    text = "🎥 Video Note"
                    media_id = message.video_note.file_id
                elif message.document:
                    text = f"📄 Document: {message.document.file_name}"
                    media_id = message.document.file_id
                elif message.audio:
                    text = f"🎵 Audio: {message.audio.title}"
                    media_id = message.audio.file_id
                else:
                    text = "📦 Media Content"
            
            # 💡 ဝန်ထမ်းဖြစ်ကြောင်း စစ်ဆေးခြင်း (Database + Anonymous Admin + Group Owner)
            is_staff = db_manager.check_if_staff(user_id)
            is_mgr = is_manager_func(user_id)
            
            # Anonymous Admin သို့မဟုတ် Group Owner စစ်ဆေးခြင်း
            if not is_staff and not is_mgr:
                if message.sender_chat and message.sender_chat.id == chat_id:
                    is_staff = True # Anonymous Admin
                elif message.from_user and message.from_user.is_bot:
                    return # Bot စာများကို ကျော်မည်
                else:
                    # Admin List ထဲတွင် ပါ/မပါ စစ်ဆေးခြင်း (Safety Net)
                    try:
                        member = bot.get_chat_member(chat_id, user_id)
                        if member.status in ['administrator', 'creator']:
                            is_staff = True
                    except: pass

            is_os_group = db_manager.check_if_os_group(chat_id)
            
            if is_os_group:
                # 💡 General Topic Fallback: 0 သို့မဟုတ် None ဖြစ်ပါက 1 ဟု သတ်မှတ်မည်
                topic_id = message.message_thread_id if (message.is_topic_message and message.message_thread_id) else 1
                
                # Smart Polling အတွက် နောက်ဆုံးဖတ်ထားသော ID ကို အမြဲ Update လုပ်မည်
                db_manager.update_last_read_id(chat_id, topic_id, message.message_id)

                if is_staff or is_mgr:
                    return

                # Customer ဆီမှ စာဝင်လာခြင်း
                # 🛡️ Dual-Run Guard: route_message က module နဲ့ handle ပြီးသွားရင် log_message ထပ်မလုပ်ပါ
                if not message.from_user.is_bot and not text.startswith('/'):
                    if not handled_by_module:
                        db_manager.log_message(message.message_id, chat_id, topic_id, user_id, text, message.date, media_id=media_id)
                        log.info(f"📩 New Pending Message from {user_id} in {chat_id} (Topic: {topic_id})")
                    else:
                        log.info(f"📩 Message from {user_id} in {chat_id} already handled by module — skipping duplicate log.")

        except Exception as e:
            log.error(f"❌ Message Handler Error: {e}")
