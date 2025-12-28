"""
run_server.py - start the ChatApp server (CLI)
"""

import logging
from ChatApp.server.server import ChatServer

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = ChatServer(host="0.0.0.0", port=None)
    print(f"Starting ChatApp Server on {server.host}:{server.port}")
    server.start()
