import logging
from typing import Any, Optional

from app.database import db

logger = logging.getLogger(__name__)


class UserManager:
    """
    User service layer.

    Responsibilities:
    - create/read users
    - save CoinW API credentials
    - validate whether a user can trade
    - update operational status
    """

    ALLOWED_STATUSES = {
        "active",
        "restricted_fee",
        "suspended",
        "pending_api",
    }

    def get_or_create_user(self, telegram_id: int, username: Optional[str] = None) -> dict[str, Any]:
        user = db.get_user_by_telegram(telegram_id)

        if user:
            return user

        logger.info("Creating new user telegram_id=%s", telegram_id)
        return db.create_user(telegram_id=telegram_id, username=username)

    def get_user(self, telegram_id: int) -> Optional[dict[str, Any]]:
        return db.get_user_by_telegram(telegram_id)

    def set_api_credentials(self, telegram_id: int, api_key: str, api_secret: str) -> bool:
        if not api_key or not api_secret:
            logger.warning("Rejected empty API credentials for telegram_id=%s", telegram_id)
            return False

        user = db.get_user_by_telegram(telegram_id)
        if not user:
            logger.warning("User not found while setting API credentials telegram_id=%s", telegram_id)
            return False

        db.set_user_api_keys(
            telegram_id=telegram_id,
            api_key=api_key.strip(),
            api_secret=api_secret.strip(),
        )

        # once credentials are set, default the user to active
        db.update_user_status(telegram_id=telegram_id, status="active")

        logger.info("API credentials saved for telegram_id=%s", telegram_id)
        return True

    def has_api_credentials(self, telegram_id: int) -> bool:
        user = db.get_user_by_telegram(telegram_id)
        if not user:
            return False

        api_key = user.get("api_key")
        api_secret = user.get("api_secret")

        return bool(api_key and api_secret)

    def get_user_status(self, telegram_id: int) -> Optional[str]:
        user = db.get_user_by_telegram(telegram_id)
        if not user:
            return None
        return user.get("status", "unknown")

    def set_user_status(self, telegram_id: int, status: str) -> bool:
        if status not in self.ALLOWED_STATUSES:
            logger.warning("Invalid user status '%s' for telegram_id=%s", status, telegram_id)
            return False

        user = db.get_user_by_telegram(telegram_id)
        if not user:
            logger.warning("User not found while updating status telegram_id=%s", telegram_id)
            return False

        db.update_user_status(telegram_id=telegram_id, status=status)
        logger.info("User status updated telegram_id=%s status=%s", telegram_id, status)
        return True

    def can_trade(self, telegram_id: int) -> tuple[bool, str | None]:
        user = db.get_user_by_telegram(telegram_id)

        if not user:
            return False, "Usuario no encontrado"

        if not user.get("api_key") or not user.get("api_secret"):
            return False, "El usuario no tiene API Key y API Secret configuradas"

        status = user.get("status", "unknown")

        if status == "active":
            return True, None

        if status == "restricted_fee":
            return False, "Usuario restringido por fee pendiente"

        if status == "suspended":
            return False, "Usuario suspendido por administración"

        if status == "pending_api":
            return False, "Usuario pendiente de configurar API"

        return False, f"Estado de usuario no válido: {status}"

    def list_active_users(self) -> list[dict[str, Any]]:
        return list(
            db.users.find(
                {
                    "status": "active",
                    "api_key": {"$ne": None},
                    "api_secret": {"$ne": None},
                }
            )
        )

    def sanitize_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """
        Safe version for sending user data to Telegram/admin views.
        Never expose full secrets.
        """
        if not user:
            return {}

        sanitized = dict(user)

        if sanitized.get("api_key"):
            key = str(sanitized["api_key"])
            sanitized["api_key"] = f"{key[:4]}***{key[-4:]}" if len(key) >= 8 else "***"

        if sanitized.get("api_secret"):
            sanitized["api_secret"] = "***"

        return sanitized


user_manager = UserManager()
