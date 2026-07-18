#!/usr/bin/env python3
"""Card fluttuante — AI Router Mode Panel (PySide6)."""
from __future__ import annotations
import json
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QGradient, QLinearGradient, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGridLayout, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QSizePolicy,
)

# Palette
C = {
    "accent": "#4ade80", "accent_bg": "#16241a", "blue": "#38bdf8",
    "warn": "#fbbf24", "err": "#f87171",
    "bg0": "#0a0e13", "bg1": "#0f1419", "bg2": "#1a2029", "bg3": "#222736",
    "border": "#2d3640", "border_bright": "#3d4a5a",
    "txt": "#e5e7eb", "muted": "#9ca3af", "faint": "#5a6470",
    "green_btn": "#238636", "gray_btn": "#21262d",
}

ROUTER = "http://localhost:8787"
MODE_FILE = Path.home() / ".claude" / "ai-router-mode"
ICON_PATH = Path.home() / ".claude" / "scripts" / "router-mode-icon.png"
DESKTOP_NAME = "router-mode-panel"

WINDOW_W, WINDOW_H = 480, 540
TITLE_H = 38
HERO_H = 56
SPACING = 8

MODES = [
    {"id": "anthropic", "icon": "🔵", "label": "Anthropic", "exec": "Claude Opus/Sonnet"},
    {"id": "minimax", "icon": "🟠", "label": "MiniMax", "exec": "M3 orch / M2.7 act"},
    {"id": "mix-am", "icon": "🔷", "label": "MixAM", "exec": "Anthropic THINK + MiniMax ACT"},
    {"id": "glm", "icon": "🟢", "label": "GLM", "exec": "GLM-5.2 orch / tiering"},
    {"id": "mix-gm", "icon": "🟢🟠", "label": "MixGM", "exec": "GLM-5.2 THINK + MiniMax ACT"},
    {"id": "mix-ag", "icon": "🔵🟢", "label": "MixAG", "exec": "Anthropic THINK + GLM ACT"},
]

def hex_c(h, a=255):
    r = int(h[1:3], 16); g = int(h[3:5], 16); b = int(h[5:7], 16)
    return QColor(r, g, b, a)

def http_get(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

def http_post(url, timeout=10):
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

def get_current():
    j = http_get(f"{ROUTER}/health")
    if j and j.get("mode"):
        return j["mode"]
    if MODE_FILE.exists():
        return MODE_FILE.read_text().strip()
    return "?"

class TitleBar(QWidget):
    def __init__(self, parent):
        super().__init__()
        self._parent = parent
        self.setFixedHeight(TITLE_H)
        self._offset = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(6)
        icon_lbl = QLabel("🔀")
        icon_lbl.setFont(QFont("Sans", 13))
        icon_lbl.setStyleSheet(f"background:transparent;color:{C['accent']}")
        layout.addWidget(icon_lbl)
        title = QLabel("AI Router")
        title.setFont(QFont("Sans", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"background:transparent;color:{C['txt']}")
        layout.addWidget(title)
        layout.addStretch()
        self._make_btn("─", "minimize", self._minimize, C["muted"], C["txt"]).setFont(QFont("Sans", 13))
        self._make_btn("↻", "restart", parent._restart, C["muted"], C["warn"]).setFont(QFont("Sans", 11))
        self._make_btn("✕", "close", parent.close, C["muted"], C["err"]).setFont(QFont("Sans", 11, QFont.Weight.Bold))

    def _make_btn(self, text, tooltip, cb, normal_col, hover_col):
        btn = QPushButton(text)
        btn.setFixedSize(28, 26)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {normal_col}; border: none; border-radius: 5px; font-size: 12px; }} QPushButton:hover {{ background: {C['bg3']}; color: {hover_col}; }}")
        btn.setToolTip(tooltip)
        btn.clicked.connect(cb)
        self.layout().addWidget(btn)
        return btn

    def _minimize(self): self._parent.hide()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._offset = e.globalPosition().toPoint() - self._parent.frameGeometry().topLeft()
    def mouseMoveEvent(self, e):
        if self._offset is not None:
            self._parent.move(e.globalPosition().toPoint() - self._offset)
    def mouseReleaseEvent(self, _): self._offset = None

class HeroWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__()
        self._dot_on = True
        self._mode = "?"
        self._exec_text = ""
        self._health = False
        self.setFixedHeight(HERO_H)
        self._timer = QTimer()
        self._timer.timeout.connect(self._blink)
        self._timer.start(900)
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("hero")
        self.setStyleSheet(f"QWidget#hero {{ background: {C['bg1']}; border: 1.5px solid {C['border']}; border-radius: 10px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)
        self._dot = QLabel("●")
        self._dot.setFont(QFont("Sans", 12))
        self._dot.setFixedWidth(16)
        self._dot.setStyleSheet(f"background:transparent;color:{C['accent']}")
        layout.addWidget(self._dot)
        info = QVBoxLayout()
        info.setSpacing(1)
        self._mode_lbl = QLabel("—")
        self._mode_lbl.setFont(QFont("Sans", 24, QFont.Weight.Bold))
        self._mode_lbl.setStyleSheet(f"background:transparent;color:{C['accent']}")
        self._exec_lbl = QLabel("")
        self._exec_lbl.setFont(QFont("Sans", 8))
        self._exec_lbl.setWordWrap(True)
        self._exec_lbl.setStyleSheet(f"background:transparent;color:{C['blue']}")
        info.addWidget(self._mode_lbl)
        info.addWidget(self._exec_lbl)
        layout.addLayout(info)
        layout.addStretch()
        self._health_lbl = QLabel("OFFLINE")
        self._health_lbl.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        self._health_lbl.setStyleSheet(f"background:transparent;color:{C['err']}")
        self._health_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._health_lbl)

    def set_state(self, mode, exec_text, health_ok):
        self._mode = mode
        self._exec_text = exec_text
        self._health = health_ok
        col = C["accent"] if health_ok else C["err"]
        self._mode_lbl.setText(mode.upper() if mode else "?")
        self._mode_lbl.setStyleSheet(f"background:transparent;color:{col}")
        self._exec_lbl.setText(exec_text)
        self._exec_lbl.setVisible(bool(exec_text))
        self._health_lbl.setText("HEALTH OK" if health_ok else "OFFLINE")
        self._health_lbl.setStyleSheet(f"background:transparent;color:{col};font-weight:bold")
        self._dot.setStyleSheet(f"background:transparent;color:{col}")
        self.setStyleSheet(f"QWidget#hero {{ background: {C['bg1']}; border: 1.5px solid {col}; border-radius: 10px; }}")

    def _blink(self):
        if self._health:
            cur = self.styleSheet()
            if "opacity:0.3" in cur:
                self._dot.setStyleSheet(f"background:transparent;color:{C['accent']}")
            else:
                self._dot.setStyleSheet(f"background:transparent;color:{C['accent']};opacity:0.4")

# ── Mode Card (148x90, pulsante 70x26 a destra) ──────────────────────────
class ModeCard(QWidget):
    CARD_W = 140
    CARD_H = 90
    BTN_W = 70
    BTN_H = 26

    def __init__(self, data, on_switch):
        super().__init__()
        self._m = data
        self._on_switch = on_switch
        self._active = False
        self._switching = False
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("modecard")
        self.setStyleSheet(f"QWidget#modecard {{ background: {C['bg1']}; border: 1.5px solid {C['border']}; border-radius: 10px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Riga 1: emoji + nome
        header = QHBoxLayout()
        header.setSpacing(4)
        header.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel(self._m["icon"])
        icon_lbl.setFont(QFont("Sans", 14))
        icon_lbl.setStyleSheet("background:transparent")
        lbl_lbl = QLabel(self._m["label"])
        lbl_lbl.setFont(QFont("Sans", 11, QFont.Weight.Bold))
        lbl_lbl.setStyleSheet(f"background:transparent;color:{C['txt']}")
        header.addWidget(icon_lbl)
        header.addWidget(lbl_lbl)
        header.addStretch()
        layout.addLayout(header)

        # Riga 2: exec text 8pt faint
        exec_lbl = QLabel(self._m["exec"])
        exec_lbl.setFont(QFont("Sans", 8))
        exec_lbl.setWordWrap(True)
        exec_lbl.setStyleSheet(f"background:transparent;color:{C['faint']}")
        exec_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(exec_lbl, 1)

        # Riga 3: pulsante ON centrato
        btn_row = QHBoxLayout()
        btn_row.setSpacing(0)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()
        self._btn = QPushButton("ON")
        self._btn.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setFixedSize(self.BTN_W, self.BTN_H)
        self._btn.clicked.connect(lambda: self._on_switch(self._m["id"]))
        btn_row.addWidget(self._btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._update_style()

    def _update_style(self):
        if self._active:
            self._btn.setText("ON")
            self._btn.setStyleSheet(f"QPushButton {{ background: {C['green_btn']}; color: #ffffff; border: none; border-radius: 6px; font-weight: bold; font-size: 9pt; }}")
            self.setStyleSheet(f"QWidget#modecard {{ background: {C['accent_bg']}; border: 1.5px solid {C['green_btn']}; border-radius: 10px; }}")
        else:
            self._btn.setText("ON")
            self._btn.setStyleSheet(f"QPushButton {{ background: {C['gray_btn']}; color: {C['muted']}; border: 1px solid {C['border']}; border-radius: 6px; font-weight: bold; font-size: 9pt; }} QPushButton:hover {{ border-color: {C['blue']}; color: {C['txt']}; }}")
            self.setStyleSheet(f"QWidget#modecard {{ background: {C['bg1']}; border: 1.5px solid {C['border']}; border-radius: 10px; }}")

    def set_active(self, active):
        self._active = active
        self._update_style()
class Card(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("AI Router")
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self._drag = QPoint()
        self._dragging = False
        self._current = "?"
        self._health_ok = False

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 110))

        container = QWidget(self)
        container.setObjectName("container")
        container.setGraphicsEffect(shadow)
        container.setStyleSheet(f"QWidget#container {{ background: {C['bg2']}; border: 1px solid {C['border']}; border-radius: 16px; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(container)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self._titlebar = TitleBar(self)
        inner.addWidget(self._titlebar)

        body = QWidget()
        body.setStyleSheet("background:transparent")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(SPACING)

        # Hero 56px
        self._hero = HeroWidget()
        body_layout.addWidget(self._hero)

        # Spacer 6px
        spacer0 = QWidget()
        spacer0.setFixedHeight(6)
        body_layout.addWidget(spacer0)

        # ── Sezione SOLO (3 colonne) ─────────────────────────────────
        solo_lbl = QLabel("SOLO")
        solo_lbl.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        solo_lbl.setStyleSheet("background:transparent;color:#5a6470")
        body_layout.addWidget(solo_lbl)

        solo_grid = QGridLayout()
        solo_grid.setSpacing(SPACING)
        solo_ids = ["anthropic", "minimax", "glm"]
        self._cards = {}
        for i, mid in enumerate(solo_ids):
            m = next(x for x in MODES if x["id"] == mid)
            card = ModeCard(m, self._do_switch)
            self._cards[mid] = card
            solo_grid.addWidget(card, 0, i)
        body_layout.addLayout(solo_grid)

        # Spacer 8px
        spacer1 = QWidget()
        spacer1.setFixedHeight(8)
        body_layout.addWidget(spacer1)

        # ── Sezione MULTI (4 card su 2 righe: 3+1) ──────────────────
        multi_lbl = QLabel("MULTI")
        multi_lbl.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        multi_lbl.setStyleSheet("background:transparent;color:#5a6470")
        body_layout.addWidget(multi_lbl)

        multi_grid = QGridLayout()
        multi_grid.setSpacing(SPACING)
        multi_ids = ["mix-am", "mix-gm", "mix-ag"]
        for i, mid in enumerate(multi_ids):
            m = next(x for x in MODES if x["id"] == mid)
            card = ModeCard(m, self._do_switch)
            self._cards[mid] = card
            row = 0 if i < 3 else 1
            col = i if i < 3 else 0
            multi_grid.addWidget(card, row, col)
        body_layout.addLayout(multi_grid)

        inner.addWidget(body)

        self._center()
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(5000)
        self._refresh()

    def _center(self):
        s = QApplication.primaryScreen()
        if s:
            r = s.availableGeometry()
            self.move(r.center().x() - WINDOW_W // 2, r.center().y() - WINDOW_H // 2)

    def _refresh(self):
        self._current = get_current()
        j = http_get(f"{ROUTER}/health")
        self._health_ok = bool(j and j.get("ok"))
        self._update_ui()

    def _update_ui(self):
        mi = next((m for m in MODES if m["id"] == self._current), None)
        icon = mi["icon"] if mi else ""
        exec_text = mi["exec"] if mi else ""
        self._hero.set_state(self._current, exec_text, self._health_ok)
        for mid, card in self._cards.items():
            card.set_active(mid == self._current)

    def _do_switch(self, mode):
        j = http_post(f"{ROUTER}/admin/mode/{mode}")
        if j and j.get("ok"):
            self._current = mode
        self._update_ui()

    def _restart(self):
        import subprocess, time
        try:
            subprocess.run(["systemctl", "--user", "restart", "ai-router"], capture_output=True, timeout=10)
            time.sleep(2.5)
        except Exception:
            pass
        self._refresh()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = True
    def mouseMoveEvent(self, e):
        if self._dragging:
            self.move(e.globalPosition().toPoint() - self._drag)
    def mouseReleaseEvent(self, _): self._dragging = False
    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Q): self.close()

def main():
    app = QApplication([])
    app.setApplicationName("AI Router")
    app.setApplicationDisplayName("AI Router")
    app.setDesktopFileName(DESKTOP_NAME)
    icon = QIcon(str(ICON_PATH))
    app.setWindowIcon(icon)
    w = Card()
    w.setWindowIcon(icon)
    w.show()
    app.exec()

if __name__ == "__main__":
    main()
