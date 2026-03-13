import logging
from typing import Optional, Dict, Any, List
from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection
from bson import ObjectId

from app.config import config

logger = logging.getLogger(__name__)


class Database:

    def __init__(self):

        self.client = MongoClient(config.mongo_uri)
        self.db = self.client[config.mongo_db_name]

        self.users: Collection = self.db["users"]
        self.trades: Collection = self.db["trades"]
        self.positions: Collection = self.db["positions"]

        self._create_indexes()

        logger.info("Database connected")

    def _create_indexes(self):

        self.users.create_index([("telegram_id", ASCENDING)], unique=True)
        self.trades.create_index([("user_id", ASCENDING)])
        self.positions.create_index([("user_id", ASCENDING)])
        self.positions.create_index([("symbol", ASCENDING)])

    # --------------------------------------------------
    # USERS
    # --------------------------------------------------

    def create_user(self, telegram_id: int, username: Optional[str]) -> Dict[str, Any]:

        user = {
            "telegram_id": telegram_id,
            "username": username,
            "api_key": None,
            "api_secret": None,
            "status": "active",
            "created_at": self._now()
        }

        result = self.users.insert_one(user)

        user["_id"] = result.inserted_id

        logger.info(f"User created {telegram_id}")

        return user

    def get_user_by_telegram(self, telegram_id: int) -> Optional[Dict[str, Any]]:

        return self.users.find_one({"telegram_id": telegram_id})

    def set_user_api_keys(self, telegram_id: int, api_key: str, api_secret: str):

        self.users.update_one(
            {"telegram_id": telegram_id},
            {
                "$set": {
                    "api_key": api_key,
                    "api_secret": api_secret
                }
            }
        )

        logger.info(f"API keys updated for {telegram_id}")

    def update_user_status(self, telegram_id: int, status: str):

        self.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": {"status": status}}
        )

    # --------------------------------------------------
    # POSITIONS
    # --------------------------------------------------

    def create_position(self, position: Dict[str, Any]):

        self.positions.insert_one(position)

    def get_open_positions(self, user_id: ObjectId) -> List[Dict[str, Any]]:

        return list(
            self.positions.find(
                {"user_id": user_id, "status": "open"}
            )
        )

    def close_position(self, position_id: ObjectId):

        self.positions.update_one(
            {"_id": position_id},
            {"$set": {"status": "closed"}}
        )

    # --------------------------------------------------
    # TRADES
    # --------------------------------------------------

    def create_trade(self, trade: Dict[str, Any]):

        self.trades.insert_one(trade)

    def get_user_trades(self, user_id: ObjectId) -> List[Dict[str, Any]]:

        return list(
            self.trades.find({"user_id": user_id})
        )

    # --------------------------------------------------
    # UTIL
    # --------------------------------------------------

    def _now(self):

        from datetime import datetime
        return datetime.utcnow()


db = Database()
