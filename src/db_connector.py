try:
    from pymongo import MongoClient
    HAS_PYMONGO = True
except ImportError:
    MongoClient = None
    HAS_PYMONGO = False

class _MongoDBConnector:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(_MongoDBConnector, cls).__new__(cls)
            cls._instance.client = None
            cls._instance.db = None
            if HAS_PYMONGO:
                try:
                    # Quick ping check (500ms timeout) so it doesn't hang if Mongo isn't running
                    client = MongoClient('mongodb://127.0.0.1:27017/', serverSelectionTimeoutMS=500)
                    client.admin.command('ping')
                    cls._instance.client = client
                    cls._instance.db = client['trading_engine']
                except Exception:
                    # MongoDB not installed or not running
                    cls._instance.db = None
        return cls._instance

# Global, persistent singleton accessor (None if Mongo is not running)
db = _MongoDBConnector().db
