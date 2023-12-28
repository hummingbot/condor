from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import AUTHORIZED_USERS


def restricted(func):
    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user_id = update.effective_user.id
        if user_id not in AUTHORIZED_USERS:
            print(f"Unauthorized access denied for {user_id}.")
            await update.message.reply_text("You are not authorized to use this bot.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapped
