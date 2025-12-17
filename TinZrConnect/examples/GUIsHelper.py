# GUIsHelper.py
import os
import math

from PyQt5 import QtCore, QtGui, QtWidgets

# You *can* keep this here if you want all TinZr GUIs to set it,
# but it's not required for the GUI itself.
os.environ["BLEAK_BACKEND"] = "dotnet"


# ================== Toggle Switch Widget ==================
class ToggleSwitch(QtWidgets.QCheckBox):
    """
    Nice big oval sliding switch:
      - OFF: gray background
      - ON : green background
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # Bigger, chunkier pill
        self._thumb_radius = 12
        self._track_radius = 12
        self._margin = 3

        self._width = 60
        self._height = 30

        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setChecked(False)
        self.setFixedSize(self._width, self._height)

        # Hide default checkbox indicator
        self.setStyleSheet("QCheckBox::indicator { width:0px; height:0px; }")

        # repaint whenever state changes
        self.stateChanged.connect(lambda _: self.update())

    def sizeHint(self):
        return QtCore.QSize(self._width, self._height)

    def hitButton(self, pos: QtCore.QPoint) -> bool:
        """
        Make the *entire* rect clickable, not just the (hidden) indicator.
        """
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = self.rect()
        x = rect.x()
        y = rect.y()
        w = rect.width()
        h = rect.height()

        # Track rect
        track_rect = QtCore.QRectF(
            x + self._margin,
            y + self._margin,
            w - 2 * self._margin,
            h - 2 * self._margin
        )

        # Background color
        if self.isChecked():
            track_color = QtGui.QColor(76, 175, 80)   # ON: green
        else:
            track_color = QtGui.QColor(110, 110, 120) # OFF: gray

        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(track_rect, self._track_radius, self._track_radius)

        # Thumb
        thumb_d = 2 * self._thumb_radius
        if self.isChecked():
            thumb_x = track_rect.right() - thumb_d
        else:
            thumb_x = track_rect.left()

        thumb_y = track_rect.center().y() - self._thumb_radius
        thumb_rect = QtCore.QRectF(thumb_x, thumb_y, thumb_d, thumb_d)

        thumb_color = QtGui.QColor(240, 240, 240)
        p.setBrush(thumb_color)
        p.drawEllipse(thumb_rect)

        p.end()


# ================== Spinner Widget ==================
class Spinner(QtWidgets.QWidget):
    def __init__(self, radius=8, line_width=2, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._radius = radius
        self._line_width = line_width

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_timeout)

        size = radius * 4
        self.setFixedSize(size, size)
        self.setVisible(False)

    def start(self):
        if not self._timer.isActive():
            self._timer.start(1000 // 24)  # ~24 FPS
            self.setVisible(True)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
        self.setVisible(False)

    def _on_timeout(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        if not self.isVisible():
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        center = self.rect().center()
        radius = self._radius

        pen = QtGui.QPen(self.palette().highlight().color(), self._line_width)
        p.setPen(pen)

        # 12 segments around the circle, fading tail
        for i in range(12):
            alpha = int(255 * (i + 1) / 12)
            color = pen.color()
            color.setAlpha(alpha)
            pen.setColor(color)
            p.setPen(pen)

            angle_deg = self._angle + i * 30
            angle = math.radians(angle_deg)

            x1 = center.x() + math.cos(angle) * (radius * 0.4)
            y1 = center.y() + math.sin(angle) * (radius * 0.4)
            x2 = center.x() + math.cos(angle) * radius
            y2 = center.y() + math.sin(angle) * radius

            p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))


# ================== Battery Widget ==================
class BatteryWidget(QtWidgets.QWidget):
    """
    Horizontal battery indicator with:
      - body + small terminal
      - fill color depending on % (green/yellow/red)
      - percentage text inside
      - emits clicked() when user left-clicks it
    """
    clicked = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = None  # None = unknown; 0–100 = valid
        self.setFixedSize(50, 25)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def sizeHint(self):
        return QtCore.QSize(80, 28)

    def setLevel(self, pct):
        """pct can be None or 0–100."""
        if pct is None:
            self._level = None
        else:
            self._level = max(0.0, min(100.0, float(pct)))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = self.rect()
        w = rect.width()
        h = rect.height()

        # ----- Geometry -----
        margin = 3
        body_rect = QtCore.QRectF(
            margin,
            margin,
            w - margin * 3,   # leave room for terminal
            h - margin * 2
        )

        term_width = 6
        term_rect = QtCore.QRectF(
            body_rect.right(),
            body_rect.center().y() - (body_rect.height() * 0.25),
            term_width,
            body_rect.height() * 0.5
        )

        # ----- Colors -----
        bg_body = QtGui.QColor("#020817")
        border  = QtGui.QColor("#E0E8FF")
        empty_fill = QtGui.QColor("#111827")

        if self._level is None:
            fill_color = empty_fill
        else:
            if self._level >= 60:
                fill_color = QtGui.QColor("#4CAF50")  # green
            elif self._level >= 30:
                fill_color = QtGui.QColor("#FFC107")  # yellow
            else:
                fill_color = QtGui.QColor("#F44336")  # red

        # ----- Draw body -----
        p.setPen(QtGui.QPen(border, 1.5))
        p.setBrush(bg_body)
        radius = body_rect.height() * 0.3
        p.drawRoundedRect(body_rect, radius, radius)

        # ----- Fill -----
        if self._level is not None:
            level_frac = self._level / 100.0
            level_frac = max(0.0, min(1.0, level_frac))

            fill_rect = QtCore.QRectF(body_rect)
            fill_rect.setWidth(body_rect.width() * level_frac)
            fill_rect.adjust(1.5, 1.5, -1.5, -1.5)

            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(fill_color)
            if fill_rect.width() > 0:
                p.drawRoundedRect(fill_rect, radius * 0.7, radius * 0.7)

        # ----- Terminal -----
        p.setPen(QtGui.QPen(border, 1.2))
        p.setBrush(bg_body)
        p.drawRoundedRect(term_rect, 2, 2)

        # ----- Text -----
        if self._level is None:
            text = "--"
        else:
            text = f"{int(round(self._level))}"

        font = QtGui.QFont(self.font())
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QtGui.QColor("#E0E8FF"))
        p.drawText(body_rect, QtCore.Qt.AlignCenter, text)

        p.end()


# ================== Theme helper ==================
def apply_tinzr_theme(widget: QtWidgets.QWidget):
    """
    Apply the TinZr dark blue theme, shared across all TinZr GUIs.
    """
    palette = widget.palette()
    bg = QtGui.QColor("#020817")       # main window background
    base = QtGui.QColor("#071529")     # controls background
    text = QtGui.QColor("#E0E8FF")     # light text
    accent = QtGui.QColor("#1E88E5")   # bright blue
    disable = QtGui.QColor("#555a70")

    palette.setColor(QtGui.QPalette.Window, bg)
    palette.setColor(QtGui.QPalette.Base, base)
    palette.setColor(QtGui.QPalette.AlternateBase, base.darker(120))
    palette.setColor(QtGui.QPalette.Text, text)
    palette.setColor(QtGui.QPalette.WindowText, text)
    palette.setColor(QtGui.QPalette.Button, base)
    palette.setColor(QtGui.QPalette.ButtonText, text)
    palette.setColor(QtGui.QPalette.Highlight, accent)
    palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disable)
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disable)
    widget.setPalette(palette)

    widget.setStyleSheet("""
    QWidget {
        background-color: #020817;
        color: #E0E8FF;
        font-family: "Segoe UI", "Roboto", sans-serif;
        font-size: 11pt;
    }
    QComboBox {
        background-color: #071529;
        border: 1px solid #1E88E5;
        border-radius: 8px;
        padding: 4px 8px;
    }
    QComboBox QAbstractItemView {
        background-color: #071529;
        selection-background-color: #1E88E5;
        selection-color: #ffffff;
    }
    QPushButton {
        background-color: #1565C0;
        border-radius: 16px;
        padding: 6px 18px;
        color: white;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: #1E88E5;
    }
    QPushButton:pressed {
        background-color: #0D47A1;
    }
    QPushButton:disabled {
        background-color: #0b2340;
        color: #70758c;
    }
    QLabel#statusLabel {
        color: #90CAF9;
        font-size: 10pt;
    }
    
    /* ---------- Tabs (QTabWidget + QTabBar) ---------- */
    QTabWidget::pane {
        border-top: 1px solid #1E88E5;
        background-color: #020817;
    }

    QTabBar::tab {
        background-color: #071529;
        color: #E0E8FF;
        padding: 6px 14px;
        margin-right: 2px;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        font-weight: 500;
    }

    QTabBar::tab:selected {
        background-color: #0D47A1;
        color: #E3F2FD;
    }

    QTabBar::tab:hover {
        background-color: #1565C0;
    }

    QTabBar::tab:!selected {
        /* slightly dimmer text for inactive tabs */
        color: #B0BEC5;
    }
    
    
    
    """)
