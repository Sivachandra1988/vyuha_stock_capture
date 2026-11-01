from pymongo import MongoClient

class _MongoDBConnector:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(_MongoDBConnector, cls).__new__(cls)
            try:
                # Maintain a single persistent connection pool for the entire process
                cls._instance.client = MongoClient('mongodb://127.0.0.1:27017/', serverSelectionTimeoutMS=5000)
                cls._instance.db = cls._instance.client['trading_engine']
            except Exception as e:
                print(f"[MongoDB] Connection failed: {e}")
                cls._instance.db = None
        return cls._instance

# Global, persistent singleton accessor
db = _MongoDBConnector().db
