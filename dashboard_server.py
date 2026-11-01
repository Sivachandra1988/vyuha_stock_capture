import os
import sys

# Add src/ to Python path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "src"))

from dashboard import server

if __name__ == "__main__":
    try:
        server.run_server()
    except KeyboardInterrupt:
        pass
