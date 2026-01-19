# TinZrComCenter.py  (PyQt5 version)
import os
import sys
from PyQt5 import QtWidgets, QtGui, QtCore

from gui.tab_wifi_hub import WifiHubTab
from gui.tab_commands import CommandsTab   

__VERSION__ = "V1.0.0"

# Use your shared visual style
try:
    # ===== Make parent "examples" directory importable so we can see GUIsHelper.py =====
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    PARENT_DIR = os.path.dirname(CURRENT_DIR)
    if PARENT_DIR not in sys.path:
        sys.path.insert(0, PARENT_DIR)
    
    from GUIsHelper import apply_tinzr_theme
except ImportError:
    apply_tinzr_theme = None


class TinZrComCenterApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("TinZr Communication Center")
        self.setWindowIcon(QtGui.QIcon("TinZr_small_logo.ico"))

        # ==== Lock physical window size: 5 in x 3 in ====
        screen = QtWidgets.QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch()  # or physicalDotsPerInch() if you want ruler-accurate

        width_in = 8.5
        height_in = 6.0
        width_px = int(width_in * dpi)
        height_px = int(height_in * dpi)

        self.setFixedSize(width_px, height_px)
        # (optional, but reinforces no resize)
        self.setMinimumSize(width_px, height_px)
        self.setMaximumSize(width_px, height_px)
        # ================================================

        # shared mapping: list of dicts {hostname, port, ssid, battery}
        self.devices = []

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        layout = QtWidgets.QVBoxLayout(central)

        self.notebook = QtWidgets.QTabWidget()
        layout.addWidget(self.notebook)
        
        
        # Create tabs (same logic: app=self so they can call back if needed)
        self.tab_wifi_hub = WifiHubTab(app=self)
        self.tab_commands = CommandsTab(app=self)

        # Add tabs
        self.notebook.addTab(self.tab_wifi_hub, "WIFI Hub")
        self.notebook.addTab(self.tab_commands, "Commands")
            
        
        
        
        
        # --------------------------------
        # Version text in the tab-bar row (top-right)
        # --------------------------------
        self.version_label = QtWidgets.QLabel(__VERSION__)
        self.version_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)

        # Clone app font and slightly reduce size (DPI-safe)
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 0.9)   # try 0.9–1.0
        self.version_label.setFont(font)

        self.version_label.setStyleSheet("color: #b0b0b0; padding: 0px 8px;")

        self.notebook.setCornerWidget(self.version_label, QtCore.Qt.TopRightCorner)
        
        
        
        
        
        # Apply TinZr theme if available
        if apply_tinzr_theme is not None:
            apply_tinzr_theme(self)


    # Kept for compatibility with old API (even though DevicesTab is not used here)
    def set_devices(self, devices):
        """
        Called by (future) FlasherTab when it knows the mapping of ports -> hostnames.
        Devices is a list of dicts: {hostname, port, ssid, battery}
        """
        self.devices = devices
        # In the original Tk version:
        # self.tab_devices.update_devices_view(devices)
        # We don't have a Qt DevicesTab yet, so we just store the list for now.


if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication(sys.argv)

    window = TinZrComCenterApp()
    window.show()

    sys.exit(app.exec_())
