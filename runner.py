import asyncio
import os
import threading
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from flask import Flask, jsonify

# Add bot directories to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'BOT'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'BOT1'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'BOT3'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'BOT4'))

# Import bot functions
try:
    from BOT.main import start_bot1
    from BOT1.main import start_bot2
    from BOT3.main import start_bot3
    from BOT4.main import start_bot4
    BOTS_AVAILABLE = True
except ImportError as e:
    print(f"❌ Import Error: {e}")
    BOTS_AVAILABLE = False

SECRET_KEY = os.getenv("RESTART_KEY", "mysecret")
PORT = int(os.getenv("PORT", 10000))

# Global variables
bots_running = False
bot_tasks = []
bot_thread = None

# ================= FLASK APP =================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return '''
    <h1>🤖 Telegram Multi-Bots Runner</h1>
    <p>Bots running via Flask + Runner.py</p>
    <p>Endpoints:</p>
    <ul>
        <li><a href="/health">/health</a> - Check bot status</li>
        <li><a href="/start">/start</a> - Start all bots</li>
        <li><a href="/stop">/stop</a> - Stop all bots</li>
        <li><a href="/restart">/restart</a> - Restart all bots</li>
        <li><a href="/restart?key=mysecret">/restart?key=mysecret</a> - Force restart</li>
    </ul>
    '''

@flask_app.route('/health')
def health_check():
    status = {
        'status': 'healthy' if bots_running else 'sleeping',
        'bots_running': bots_running,
        'bots_available': BOTS_AVAILABLE,
        'port': PORT,
        'message': 'All bots running' if bots_running else 'Bots not started'
    }
    return jsonify(status)

@flask_app.route('/start')
def start_bots_route():
    global bots_running
    
    if not BOTS_AVAILABLE:
        return jsonify({'error': 'Bot modules not found'}), 500
    
    if bots_running:
        return jsonify({'message': 'Bots already running'})
    
    # Start bots in background
    start_bots_background()
    return jsonify({'message': 'Starting all bots...'})

@flask_app.route('/stop')
def stop_bots_route():
    global bots_running, bot_tasks
    
    if not bots_running:
        return jsonify({'message': 'Bots not running'})
    
    # Cancel all bot tasks
    for task in bot_tasks:
        if not task.done():
            task.cancel()
    
    bots_running = False
    bot_tasks = []
    
    return jsonify({'message': 'Stopping all bots...'})

@flask_app.route('/restart')
def restart_bots_route():
    # Check for secret key
    from flask import request
    key = request.args.get('key', '')
    
    if key == SECRET_KEY:
        print("🔄 Force restart requested via secret key")
        os._exit(0)  # Force restart for Render/Koyeb
    
    # Normal restart
    stop_bots_route()
    start_bots_route()
    return jsonify({'message': 'Restarting all bots...'})

# ================= BOT MANAGEMENT =================
def start_bots_background():
    """Start all bots in background thread"""
    global bots_running, bot_thread, bot_tasks
    
    if not BOTS_AVAILABLE or bots_running:
        return
    
    bots_running = True
    
    def run_all_bots_sync():
        async def run_all_bots():
            try:
                print("🚀 Starting all bots...")
                
                # Store tasks globally
                global bot_tasks
                tasks = [
                    asyncio.create_task(start_bot1()),
                    asyncio.create_task(start_bot2()),
                    asyncio.create_task(start_bot3()),
                    asyncio.create_task(start_bot4())
                ]
                bot_tasks = tasks
                
                # Run all tasks
                await asyncio.gather(*tasks, return_exceptions=True)
                
            except asyncio.CancelledError:
                print("⏹️ Bots stopped")
            except Exception as e:
                print(f"❌ Bot Error: {e}")
        
        # Run in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_all_bots())
    
    # Start in separate thread
    bot_thread = threading.Thread(target=run_all_bots_sync, daemon=True)
    bot_thread.start()
    print("✅ All bots started in background")

# ================= OLD HTTP HANDLER (for compatibility) =================
class RestartHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == f"/restart?key={SECRET_KEY}":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Restarting bot...")
            print("🔄 Force restart via old HTTP handler")
            os._exit(0)
        else:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
    
    def log_message(self, format, *args):
        # Suppress log messages
        pass

def run_old_server():
    """Run old HTTP server for compatibility"""
    try:
        server = HTTPServer(("", 10001), RestartHandler)
        print("🌐 Old HTTP server running on port 10001")
        server.serve_forever()
    except Exception as e:
        print(f"❌ Old server error: {e}")

# ================= MAIN FUNCTION =================
def run_flask_server():
    """Run Flask server with waitress"""
    import waitress
    print(f"🌐 Flask server starting on port {PORT}...")
    waitress.serve(flask_app, host='0.0.0.0', port=PORT)

def main():
    print("=" * 50)
    print("🤖 Telegram Multi-Bots Runner")
    print("=" * 50)
    print(f"📡 Port: {PORT}")
    print(f"🔑 Restart Key: {SECRET_KEY}")
    print(f"🤖 Bots Available: {BOTS_AVAILABLE}")
    print("=" * 50)
    
    # Start old HTTP server in background (for compatibility)
    old_server_thread = threading.Thread(target=run_old_server, daemon=True)
    old_server_thread.start()
    
    # Auto-start bots if configured
    if os.getenv('AUTO_START_BOTS', 'true').lower() == 'true':
        print("⚡ Auto-starting bots...")
        start_bots_background()
    else:
        print("⏸️  Auto-start disabled, use /start endpoint")
    
    # Run Flask app (blocking)
    run_flask_server()

if __name__ == "__main__":
    main()
