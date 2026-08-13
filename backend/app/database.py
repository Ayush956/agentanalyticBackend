from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database

from app.config import get_settings

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = MongoClient(settings.mongodb_uri)
    return _client


def get_database() -> Database:
    settings = get_settings()
    return get_client()[settings.mongodb_db_name]


def ping_database() -> bool:
    get_client().admin.command("ping")
    return True
