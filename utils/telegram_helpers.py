"""Telegram helper utilities for reusing callback-based handlers from commands."""


class MockMessage:
    """Wrapper around a real message to provide edit_text interface."""

    def __init__(self, msg):
        self._msg = msg
        self.message_id = msg.message_id
        self.chat_id = msg.chat_id

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        return await self._msg.edit_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )

    async def delete(self):
        return await self._msg.delete()

    def get_bot(self):
        return self._msg.get_bot()


class MockQuery:
    """Mock query object to reuse callback-based functions from commands."""

    def __init__(self, message, from_user):
        self.message = message
        self.from_user = from_user
        self.data = ""

    async def answer(self, text="", show_alert=False):
        pass


async def create_mock_query_from_message(update, initial_text="Loading..."):
    """Create a mock query from a message update for reusing callback handlers."""
    msg = await update.message.reply_text(initial_text)
    return MockQuery(MockMessage(msg), update.effective_user)
