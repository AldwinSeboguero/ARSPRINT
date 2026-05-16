import os
import threading
import time
import platform
import shutil
from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
from pypdf import PdfReader  # Swapped from fitz to pypdf for Orange Pi One compatibility

# --- HARDWARE MOCKING ---
if platform.system() == "Darwin":
    class GPIO_Mock:
        BOARD, H3, IN, PUD_UP, FALLING = "BOARD", "H3", "IN", "PUD_UP", "FALLING"
        def setboard(self, x): pass
        def setmode(self, x): pass
        def setup(self, x, y, pull_up_down=None): pass
        def add_event_detect(self, x, y, callback=None, bouncetime=None): pass
        def cleanup(self): pass
    GPIO = GPIO_Mock()
else:
    import OPi.GPIO as GPIO

# --- CONFIGURATION ---
class Config:
    UPLOAD_FOLDER = 'uploads'
    PRICES = {
        'short': (2.00, 5.00),
        'a4':    (2.50, 7.00),
        'long':  (3.00, 10.00)
    }
    COIN_PIN = 12
    SECRET_KEY = os.urandom(24)

app = Flask(__name__)
app.config.from_object(Config)
socketio = SocketIO(app, cors_allowed_origins="*")

session_data = {
    "filename": None,
    "bw_pages": 0,
    "color_pages": 0,
    "paper_size": "NONE",
    "copies": 1,
    "amount_needed": 0,
    "coins_inserted": 0,
    "status": "Ready"
}

def reset_session():
    global session_data
    if os.path.exists(Config.UPLOAD_FOLDER):
        try: shutil.rmtree(Config.UPLOAD_FOLDER)
        except: pass
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    session_data.update({
        "filename": None, "bw_pages": 0, "color_pages": 0,
        "paper_size": "NONE", "copies": 1, "amount_needed": 0, 
        "coins_inserted": 0, "status": "Ready"
    })

# --- LIGHTWEIGHT PDF ANALYSIS (Orange Pi Friendly) ---
def analyze_pdf(path):
    reader = PdfReader(path)
    bw, color = 0, 0
    
    for page in reader.pages:
        is_color = False
        
        # Look inside the page structural objects for color markers
        if "/Resources" in page:
            resources = page["/Resources"]
            res_str = str(resources)
            
            # Common markers that signal colored graphics/text elements or RGB/CMYK setups
            if "/DeviceRGB" in res_str or "/ColorSpace" in res_str or "/CMYK" in res_str:
                is_color = True
                
        if is_color:
            color += 1
        else:
            bw += 1
            
    return bw, color

def trigger_print():
    global session_data
    session_data["status"] = "Printing..."
    socketio.emit('status_update', {"status": "Printing..."})
    def run():
        time.sleep(5)
        session_data["status"] = "Success!"
        socketio.emit('status_update', {"status": "Success!"})
        time.sleep(3)
        reset_session()
        socketio.emit('balance_update', session_data)
    threading.Thread(target=run).start()

# --- HARDWARE ---
# GPIO.setboard(GPIO.H3)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(Config.COIN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def on_coin_dropped(channel):
    global session_data
    if session_data["amount_needed"] > 0:
        session_data["coins_inserted"] += 1
        socketio.emit('balance_update', session_data)
        if session_data["coins_inserted"] >= session_data["amount_needed"]:
            trigger_print()

GPIO.add_event_detect(Config.COIN_PIN, GPIO.FALLING, callback=on_coin_dropped, bouncetime=200)

@app.route('/')
def index():
    reset_session()
    return render_template('index.html', data=session_data)

@app.route('/upload', methods=['POST'])
def upload():
    global session_data
    file = request.files.get('file')
    selected_size = request.form.get('paper_size')
    num_copies = int(request.form.get('copies', 1))
    
    if file and file.filename.endswith('.pdf') and selected_size:
        path = os.path.join(Config.UPLOAD_FOLDER, secure_filename(file.filename))
        file.save(path)
        bw, col = analyze_pdf(path)
        bw_p, col_p = Config.PRICES.get(selected_size)
        
        # Calculate total with copies
        total = ((bw * bw_p) + (col * col_p)) * num_copies
        
        session_data.update({
            "filename": file.filename, "bw_pages": bw * num_copies, 
            "color_pages": col * num_copies, "paper_size": selected_size.upper(),
            "copies": num_copies, "amount_needed": total,
            "coins_inserted": 0, "status": "Insert Coins"
        })
        return render_template('index.html', data=session_data)
    return redirect(url_for('index'))

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8001)