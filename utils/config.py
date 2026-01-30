import os

from dotenv import load_dotenv

load_dotenv()

# Primary admin user ID - this user has full control over the bot
# Set via ADMIN_USER_ID environment variable
ADMIN_USER_ID = None
_admin_id_str = os.environ.get("ADMIN_USER_ID", "").strip()
if _admin_id_str:
    try:
        ADMIN_USER_ID = int(_admin_id_str)
    except ValueError:
        pass

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# LLM API Keys - configure at least one for /chat functionality
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")