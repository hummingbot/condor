import os

from dotenv import load_dotenv

load_dotenv()

AUTHORIZED_USERS = [
    int(user_id) for user_id in os.environ.get("AUTHORIZED_USERS", "").split(",")
]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")