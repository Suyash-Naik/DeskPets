1. System Overview & ArchitecturePrimary Goal:

Create a lightweight, frameless, always-on-top desktop companion in Python that monitors system performance (CPU, RAM, GPU) and reflects computer state via state-driven cat sprite animations.Tech Stack:Language: Python 3.10+GUI Framework: PyQt6 / PySide6 (Native transparent frameless windowing, OS event transparency, click-through management).

System Metrics: psutil (CPU/RAM/Disk), gputil / pynvml (NVIDIA GPU metrics), PyObjC (macOS native performance/battery hooks).

Image Processing: Pillow (PIL) for slicing the $1536 \times 1872$ spritesheet grid into individual $192 \times 208$ animation sequences based on the JSON layout.  

2. Core Functional RequirementsA. Window & Rendering Engine

Frameless & Transparent: Operates inside a borderless Qt.WindowType.FramelessWindowHint surface with full alpha channel background transparency (Qt.WidgetAttribute.WA_TranslucentBackground).Always-on-Top & Dragging: Set to Qt.WindowType.WindowStaysOnTopHint. Users can left-click and drag the pet anywhere on the screen.Click-Through Support: Toggleable via tray menu (allowing clicks to pass straight through to windows underneath when working)

## B. Sprite Slicer & Animation State MachineSheet Parsing: 

Load layla-and-chico-pet-spritesheet.png using parameters from layla-and-chico-pet-spritesheet.json ($8 \times 9$ grid, $192 \times 208\text{ px}$ frames).  State Mapping:Hardware / App StateTrigger LogicSpritesheet Row  JSONFrame Count  JSONNormal / IdleCPU $< 20\%$, RAM $< 60\%$Row 0: idle-attentive6Low Activity / IdleUser inactive $> 5\text{ min}$Row 3: waiting-deep-sleep / Row 4: waiting-dozy4 / 5Moderate WorkloadCPU $20\text{–}60\%$Row 1: running-playful-roll / Row 7: running-busy-play8 / 6High Workload / StressCPU $> 80\%$ OR RAM $> 85\%$Row 2: running-playful-jump8System Warning / ErrorsGPU/CPU Temp $> 80^\circ\text{C}$ or High SwapRow 5: blocked-grumpy8 Input Needed or prompt activeRow 6: needs-input-sad6Task Completed / SuccessCPU spike drops back to idleRow 8: ready-pleased6C. System Telemetry Engine (Phase 1 Monitor)Polling thread running on a 1–2 second interval updating system metrics without blocking the main Qt GUI thread.Hover Tooltip / Popup Card: Hovering over Chico and Layla reveals a clean, semi-transparent mini-card showing real-time stats:CPU Load (%) & TemperatureRAM Usage (%)GPU Load & VRAM Usage (%)Battery Level (%)3. Recommended Project File StructurePlaintextdesktop-pet/
├── assets/
│   ├── layla-and-chico-pet-spritesheet.png
│   └── layla-and-chico-pet-spritesheet.json
├── src/
│   ├── __init__.py
│   ├── main.py                # App entry point & SystemTray setup
│   ├── ui/
│   │   ├── pet_window.py      # Frameless overlay window & drag logic
│   │   └── stats_card.py      # Telemetry hover tooltip card
│   ├── core/
│   │   ├── sprite_loader.py   # Slices JSON/PNG into QPixmap frames
│   │   ├── animator.py        # QTimer-driven frame player & state switcher
│   │   └── telemetry.py       # Background thread monitoring CPU/RAM/GPU
│   └── config.py              # Threshold settings and update intervals
├── requirements.txt
└── README.md
4. Phase 1 Implementation Code (Working Prototype)Pythonimport sys
import json
from PIL import Image
from PyQt6.QtCore import Qt, QTimer, QPoint, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout
import psutil

# Configuration & Asset Loading
ASSET_JSON = "layla-and-chico-pet-spritesheet.json"
ASSET_PNG = "layla-and-chico-pet-spritesheet.png"

class TelemetryThread(QThread):
    stats_updated = pyqtSignal(dict)

    def run(self):
        while True:
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory().percent
            self.stats_updated.emit({"cpu": cpu, "ram": ram})

class DesktopPet(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.SubWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.label = QLabel(self)
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        self.setLayout(layout)

        self.drag_position = QPoint()
        self.animations = {}
        self.current_state = "idle-attentive"
        self.current_frame = 0

        self.load_sprites()
        
        # Telemetry
        self.telemetry = TelemetryThread()
        self.telemetry.stats_updated.connect(self.handle_telemetry)
        self.telemetry.start()

        # Animation Loop (120ms per frame)
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_frame)
        self.anim_timer.start(120)

        self.show()

    def load_sprites(self):
        with open(ASSET_JSON, 'r') as f:
            data = json.load(f)
        
        sheet = Image.open(ASSET_PNG).convert("RGBA")
        frame_w, frame_h = 192, 208
        
        for idx, row_data in enumerate(data['rows']):
            name = row_data['name']
            frames = []
            for col in range(row_data['frames']):
                x = col * frame_w
                y = idx * frame_h
                crop = sheet.crop((x, y, x + frame_w, y + frame_h))
                
                # Convert PIL to QPixmap
                qimg = QImage(crop.tobytes(), crop.width, crop.height, QImage.Format.Format_RGBA8888)
                frames.append(QPixmap.fromImage(qimg))
            self.animations[name] = frames

    def handle_telemetry(self, stats):
        cpu, ram = stats['cpu'], stats['ram']
        self.setToolTip(f"Chico & Layla State\nCPU: {cpu}%\nRAM: {ram}%")
        
        # State logic
        if cpu > 75:
            new_state = "running-playful-jump"
        elif cpu > 30:
            new_state = "running-busy-play"
        else:
            new_state = "idle-attentive"

        if new_state != self.current_state:
            self.current_state = new_state
            self.current_frame = 0

    def update_frame(self):
        frames = self.animations.get(self.current_state, [])
        if frames:
            self.current_frame = (self.current_frame + 1) % len(frames)
            self.label.setPixmap(frames[self.current_frame])

    # Mouse Dragging Handlers
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    pet = DesktopPet()
    sys.exit(app.exec())