import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from BOT.main import start_bot1
from BOT1.main import start_bot2
from BOT3.main import start_bot3

SECRET_KEY = os.getenv("RESTART_KEY", "mysecret")

class RestartHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == f"/restart?key={SECRET_KEY}":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Restarting bot...")
            os._exit(0)  # Render auto-restart karega
        else:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")

def run_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("", port), RestartHandler)
    server.serve_forever()

async def main():
    threading.Thread(target=run_server, daemon=True).start()

    await asyncio.gather(
        start_bot1(),
        start_bot2(),
        start_bot3()
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
