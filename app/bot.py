import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

from app.config import config
from app.user_manager import user_manager

logger = logging.getLogger(__name__)


bot = Bot(token=config.telegram_token)
dp = Dispatcher()


# -------------------------------
# START
# -------------------------------

@dp.message(Command("start"))
async def start_command(message: Message):

    telegram_id = message.from_user.id
    username = message.from_user.username

    user = user_manager.get_or_create_user(
        telegram_id=telegram_id,
        username=username
    )

    text = (
        "🤖 *NeoTrade Bot*\n\n"
        "Bienvenido.\n\n"
        "Para comenzar necesitas conectar tu cuenta de CoinW.\n\n"
        "Usa:\n"
        "`/set_api_key TU_API_KEY`\n"
        "`/set_api_secret TU_API_SECRET`"
    )

    await message.answer(text, parse_mode="Markdown")


# -------------------------------
# SET API KEY
# -------------------------------

@dp.message(Command("set_api_key"))
async def set_api_key(message: Message):

    telegram_id = message.from_user.id

    args = message.text.split()

    if len(args) != 2:
        await message.answer("Uso correcto:\n/set_api_key TU_API_KEY")
        return

    api_key = args[1]

    user_manager.set_api_credentials(
        telegram_id=telegram_id,
        api_key=api_key,
        api_secret=""
    )

    await message.answer("API Key guardada.\nAhora envía:\n/set_api_secret TU_API_SECRET")


# -------------------------------
# SET API SECRET
# -------------------------------

@dp.message(Command("set_api_secret"))
async def set_api_secret(message: Message):

    telegram_id = message.from_user.id

    args = message.text.split()

    if len(args) != 2:
        await message.answer("Uso correcto:\n/set_api_secret TU_API_SECRET")
        return

    api_secret = args[1]

    user = user_manager.get_user(telegram_id)

    if not user:
        await message.answer("Usuario no encontrado. Usa /start primero.")
        return

    api_key = user.get("api_key")

    if not api_key:
        await message.answer("Primero debes enviar tu API Key con /set_api_key")
        return

    user_manager.set_api_credentials(
        telegram_id=telegram_id,
        api_key=api_key,
        api_secret=api_secret
    )

    await message.answer("✅ Cuenta conectada correctamente.")


# -------------------------------
# STATUS
# -------------------------------

@dp.message(Command("status"))
async def status(message: Message):

    telegram_id = message.from_user.id

    user = user_manager.get_user(telegram_id)

    if not user:
        await message.answer("Usuario no encontrado.")
        return

    safe_user = user_manager.sanitize_user(user)

    text = (
        "📊 *Estado del usuario*\n\n"
        f"ID: `{safe_user.get('telegram_id')}`\n"
        f"Status: `{safe_user.get('status')}`\n"
        f"API Key: `{safe_user.get('api_key')}`"
    )

    await message.answer(text, parse_mode="Markdown")


# -------------------------------
# RUN BOT
# -------------------------------

async def run_bot():

    logger.info("Starting Telegram bot")

    await dp.start_polling(bot)
