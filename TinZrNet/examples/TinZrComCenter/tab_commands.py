# tab_commands.py
import os
import sys
from PyQt5 import QtCore, QtWidgets

try:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    PARENT_DIR = os.path.dirname(CURRENT_DIR)
    if PARENT_DIR not in sys.path:
        sys.path.insert(0, PARENT_DIR)

    from GUIsHelper import apply_tinzr_theme
except ImportError:
    apply_tinzr_theme = None


class CommandsTab(QtWidgets.QWidget):
    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app
        self._build_ui()

        if apply_tinzr_theme is not None:
            apply_tinzr_theme(self)

    # ---------------- UI -----------------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QtWidgets.QLabel("TinZr Command Reference")
        font = title.font()
        font.setPointSize(10)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "Commands understood by TinZrHubCommands on the node.\n"
            "Type these into the WIFI / BLE hub send box exactly as shown."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray; font-size: 9pt;")
        layout.addWidget(desc)

        # ---- Scrollable rich-text area ----
        self.text = QtWidgets.QTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(500)
        self.text.setMinimumWidth(720)
        self.text.setStyleSheet(
            """
            QTextEdit {
                background-color: #121212;
                color: #e0e0e0;
                border: 1px solid #444;
                font-family: Consolas, monospace;
                font-size: 10pt;
            }
            """
        )

        # =====================
        # HTML REFERENCE
        # =====================
        self.text.setHtml(
            """
<html>
<head>
<style>
    body {
        background-color: #121212;
        color: #e0e0e0;
        font-family: Consolas, monospace;
        font-size: 10pt;
    }
    h1 {
        color: #7bd88f;
        font-size: 14pt;
        margin-bottom: 4px;
    }
    h2 {
        color: #ffcc66;
        font-size: 11pt;
        margin-top: 14px;
        margin-bottom: 4px;
    }
    .section-sep {
        color: #555;
        margin: 6px 0;
    }
    .codeblock {
        background-color: #1b1b1b;
        border-radius: 6px;
        padding: 6px 8px;
        margin: 4px 0 10px 0;
        white-space: pre;
    }
    .comment {
        color: #999;
    }
    .bullet {
        color: #bbbbbb;
    }
</style>
</head>
<body>

<h1>🧠 TinZr Node – Command Reference</h1>
<div class="comment">Handled by <b>TinZrHubCommands</b> on the node.</div>

<div class="section-sep">──────────────────────────────────────────────</div>

<h2>⚙ SOFT POWER CONTROL</h2>

<div class="codeblock">ON</div>
<div class="bullet">• Turn TinZr soft ON (wake). </div>
<div class="comment">
  C++ typical:<br>
  — Calls <b>_cmdSoftOn()</b><br>
  — <code>TinZr.softOn()</code><br>
  — Node loop becomes active again (<code>TinZr.isSoftOn()==true</code>)
</div>

<div class="codeblock">OFF</div>
<div class="bullet">• Turn TinZr soft OFF (sleep / idle).</div>
<div class="comment">
  C++ typical:<br>
  — Calls <b>_cmdSoftOff()</b><br>
  — <code>TinZr.softOff()</code><br>
  — Node loop early-exits (no I/O, no sensors, no hub logic)
</div>

<br>

<h2>💡 LED CONTROL</h2>

<div class="codeblock">LED &lt;R&gt; &lt;G&gt; &lt;B&gt; [BR]</div>
<div class="bullet">• Set LED color with optional brightness (0–255).</div>
<div class="comment">
  Parsed by <b>_cmdLed()</b><br>
  — RGB clamped to 0–255<br>
  — BR defaults to 255 if omitted<br>
  — <code>_core.setLED(r,g,b,br)</code>
</div>

<div class="codeblock">
LED 255 0 0 50     # red, dim
LED 0 255 0 20     # green, soft
LED 0 0 255        # blue, full power
</div>

<div class="codeblock">LED_OFF</div>
<div class="bullet">• Turn ONLY the LED off (device stays ON).</div>
<div class="comment">
  — Calls <b>_cmdOff()</b><br>
  — <code>_core.ledOff()</code><br>
  — Soft power state does NOT change.
</div>

<br>

<h2>📡 PING / PONG TEST</h2>

<div class="codeblock">PING</div>
<div class="bullet">• Connectivity test.</div>
<div class="comment">
  <b>_cmdPing()</b><br>
  — Responds over TCP with:<br>
  <code>PONG</code>
</div>

<div class="codeblock">
PING
# response
PONG
</div>

<br>

<h2>🔋 BATTERY</h2>

<div class="codeblock">BAT</div>
<div class="bullet">• Request battery voltage and %.</div>
<div class="comment">
  <b>_cmdBattery()</b><br>
  Responds:<br>
  <code>BAT &lt;voltage&gt; &lt;percent&gt;</code>
</div>

<div class="codeblock">
BAT
# response example
BAT 3.742 87
</div>

<br>

<h2>📟 DIGITAL OUTPUT</h2>

<div class="codeblock">DIG &lt;pin&gt; &lt;level&gt;</div>
<div class="bullet">• Write a digital pin HIGH or LOW.</div>
<div class="comment">
  Accepted levels:<br>
  — HIGH / 1<br>
  — LOW / 0
</div>

<div class="codeblock">
DIG 5 HIGH
DIG 5 LOW
DIG 13 1
DIG 13 0
</div>

<br>

<h2>📈 ANALOG OUTPUT (PWM)</h2>

<div class="codeblock">ANA &lt;pin&gt; &lt;value&gt;</div>
<div class="bullet">• PWM write on a pin (0–255).</div>

<div class="codeblock">
ANA 4 128
ANA 4 255
ANA 4 0
</div>

<br>

<h2>📝 NOTES</h2>

<div class="bullet">
• Commands are plain ASCII text, space-separated.<br>
• Parsed in <b>TinZrHubCommands::handleNetMessage()</b>.<br>
• Unknown commands are ignored quietly.<br>
• Some commands return responses (PING / BAT).<br>
• LED_OFF does not affect power state.<br>
• OFF disables entire node logic (unless waking with ON).
</div>

</body>
</html>
            """
        )

        layout.addWidget(self.text, 1)
