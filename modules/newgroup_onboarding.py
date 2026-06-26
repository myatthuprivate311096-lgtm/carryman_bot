"""General topic onboarding after /newgroup — price list, T&C button, pin feedback link."""
import telebot
import telebot.apihelper as apihelper
import config
from logger import log

TOS_CALLBACK_PREFIX = "ng_tos_"


def _general_thread_kw():
    return {"message_thread_id": config.NEWGROUP_GENERAL_TOPIC_ID}


def _pin_general_message(bot, chat_id, message_id):
    """Pin inside General forum topic (pyTelegramBotAPI lacks message_thread_id)."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "message_thread_id": config.NEWGROUP_GENERAL_TOPIC_ID,
        "disable_notification": True,
    }
    return apihelper._make_request(bot.token, "pinChatMessage", params=payload, method="post")


def _tos_markup(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            config.NEWGROUP_TOS_BUTTON,
            callback_data=f"{TOS_CALLBACK_PREFIX}{chat_id}",
        )
    )
    return markup


def send_general_onboarding(bot, chat_id):
    """Send 1.jpg (price list) then 2.jpg (T&C + agree button) in General topic."""
    thread_kw = _general_thread_kw()
    img1 = config.get_newgroup_general_image(1)
    img2 = config.get_newgroup_general_image(2)

    if img1:
        try:
            with open(img1, "rb") as photo:
                bot.send_photo(
                    chat_id,
                    photo,
                    caption=config.NEWGROUP_PRICE_CAPTION,
                    **thread_kw,
                )
            log.info(f"📷 Newgroup price list sent to {chat_id}")
        except Exception as e:
            log.warning(f"⚠️ Failed to send newgroup image 1 to {chat_id}: {e}")
    else:
        log.warning(f"⚠️ Newgroup image 1 not found for {chat_id}")

    if img2:
        try:
            with open(img2, "rb") as photo:
                bot.send_photo(
                    chat_id,
                    photo,
                    caption=config.NEWGROUP_TOS_CAPTION,
                    reply_markup=_tos_markup(chat_id),
                    **thread_kw,
                )
            log.info(f"📷 Newgroup T&C sent to {chat_id}")
        except Exception as e:
            log.warning(f"⚠️ Failed to send newgroup image 2 to {chat_id}: {e}")
    else:
        log.warning(f"⚠️ Newgroup image 2 not found for {chat_id}")


def register_handlers(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith(TOS_CALLBACK_PREFIX))
    def on_tos_agree(call):
        try:
            chat_id = int(call.data[len(TOS_CALLBACK_PREFIX):])
        except ValueError:
            bot.answer_callback_query(call.id, "❌ Invalid request.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "✅ ကျေးဇူးတင်ပါတယ်")

        try:
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None,
            )
        except Exception:
            pass

        thread_kw = _general_thread_kw()
        try:
            sent = bot.send_message(chat_id, config.NEWGROUP_TOS_THANKYOU, **thread_kw)
            _pin_general_message(bot, chat_id, sent.message_id)
            log.info(f"📌 Pinned TOS thank-you in {chat_id} topic {config.NEWGROUP_GENERAL_TOPIC_ID}")
        except Exception as e:
            log.error(f"❌ TOS thank-you/pin failed for {chat_id}: {e}")
            try:
                bot.answer_callback_query(call.id, "❌ ပို့ခြင်း မအောင်မြင်ပါ။", show_alert=True)
            except Exception:
                pass
