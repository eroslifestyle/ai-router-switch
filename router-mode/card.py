#!/usr/bin/env python3
"""Card fluttuante — AI Router Mode Panel (PySide6).
Hero chiaro, grid modi, titlebar con azioni. Finestra normale (no always-on-top,
icona di avvio in taskbar via desktopFileName).
Lancia: routestats card   ·   drag to move   ·   X o Esc to close.
"""
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

# ── Palette OKLCH-conscious (hex per compatibilità PySide6) ──────────────────
C = {
    "accent":     "#4ade80",   # verde chiaro
    "accent_dim": "#2d7a4d",   # verde scuro per testo su bg
    "accent_bg":   "#16241a",   # bg attivo
    "accent_glow":"#4ade8033", # glow transparente
    "blue":       "#38bdf8",   # info / exec
    "blue_dim":    "#1e4a6e",   # blue scuro
    "warn":       "#fbbf24",   # warning
    "err":        "#f87171",   # error
    "bg0":        "#0a0e13",   # sfondo finestra
    "bg1":        "#0f1419",   # surface 1
    "bg2":        "#1a2029",   # surface 2
    "bg3":        "#222736",   # surface 3 / btn off
    "border":     "#2d3640",   # bordi
    "border_bright": "#3d4a5a", # bordi hover
    "txt":        "#e5e7eb",   # testo principale
    "muted":      "#9ca3af",   # testo secondario
    "faint":      "#5a6470",   # testo terziario
    # per card widget
    "green_btn":  "#238636",
    "gray_btn":   "#21262d",
}

# Punto unico: ai-router :8787 (il proxy :9988 non esiste più)
ROUTER = "http://localhost:8787"
MODE_FILE = Path.home() / ".claude" / "ai-router-mode"
ICON_PATH = Path.home() / ".claude" / "scripts" / "router-mode-icon.png"
DESKTOP_NAME = "router-mode-panel"

MODES = [
    {"id": "anthropic", "icon": "🔵", "label": "Anthropic",
     "exec": "Claude Opus/Sonnet puro. Nessun routing."},
    {"id": "minimax", "icon": "🟠", "label": "MiniMax",
     "exec": "M3 orchestratore + M2.7 esecutore. Economia senza limiti."},
    {"id": "mixed", "icon": "🔷", "label": "Mixed",
     "exec": "Anthropic orchestra → MiniMax esegue. Bidirezionale."},
    {"id": "inverse", "icon": "🔶", "label": "Inverse",
     "exec": "M3 think · Opus OPPOSE loop × 2 · M2.7 act."},
    {"id": "glm", "icon": "🟢", "label": "GLM",
     "exec": "GLM-5.2 classifica → tier turbo/4.7/5.2. Peak-aware."},
    {"id": "glm-minimax", "icon": "🟢🟠", "label": "GLM + MM",
     "exec": "GLM-5.2 think → MiniMax act → GLM verify."},
    {"id": "anthropic-glm", "icon": "🔵🟢", "label": "Ant + GLM",
     "exec": "Anthropic orchestra → GLM tiered act → verify T2."},
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


# ── TitleBar ────────────────────────────────────────────────────────────────
class TitleBar(QWidget):
    def __init__(self, parent):
        super().__init__()
        self._parent = parent
        self.setFixedHeight(38)
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
        self._make_btn("✕", "close", parent.close, C["muted"], C["err"]).setFont(QFont("Sans", 11, QFont.Bold))

    def _make_btn(self, text, tooltip, cb, normal_col, hover_col):
        btn = QPushButton(text)
        btn.setFixedSize(28, 26)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {normal_col};
                border: none;
                border-radius: 5px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {C['bg3']};
                color: {hover_col};
            }}
            QPushButton:pressed {{
                background: {C['border']};
            }}
        """)
        btn.setToolTip(tooltip)
        btn.clicked.connect(cb)
        self.layout().addWidget(btn)
        return btn

    def _minimize(self):
        self._parent.hide()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._offset = e.globalPosition().toPoint() - self._parent.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._offset is not None:
            self._parent.move(e.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, _):
        self._offset = None


# ── Hero Widget ──────────────────────────────────────────────────────────
class HeroWidget(QWidget):
    def __init__(self, parent):
        super().__init__()
        self._parent = parent
        self._dot_on = True
        self._mode = "?"
        self._exec_text = ""
        self._health = False
        self.setFixedHeight(72)
        self._timer = QTimer()
        self._timer.timeout.connect(self._blink)
        self._timer.start(900)
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("hero")
        self.setStyleSheet(f"""
            QWidget#hero {{
                background: {C['bg1']};
                border: 1.5px solid {C['border']};
                border-radius: 10px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self._dot = QLabel("●")
        self._dot.setFont(QFont("Sans", 14))
        self._dot.setFixedWidth(18)
        self._dot.setStyleSheet("background:transparent;color:{}".format(C["accent"]))
        layout.addWidget(self._dot)

        info = QVBoxLayout()
        info.setSpacing(2)
        self._mode_lbl = QLabel("—")
        self._mode_lbl.setFont(QFont("Sans", 28, QFont.Weight.Bold))
        self._mode_lbl.setStyleSheet(f"background:transparent;color:{C['accent']}")
        self._exec_lbl = QLabel("")
        self._exec_lbl.setFont(QFont("Sans", 9))
        self._exec_lbl.setWordWrap(True)
        self._exec_lbl.setStyleSheet(f"background:transparent;color:{C['blue']}")
        info.addWidget(self._mode_lbl)
        info.addWidget(self._exec_lbl)
        layout.addLayout(info)

        layout.addStretch()

        self._health_lbl = QLabel("OFFLINE")
        self._health_lbl.setFont(QFont("Sans", 9, QFont.Weight.Bold))
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
        self.setStyleSheet(f"""
            QWidget#hero {{
                background: {C['bg1']};
                border: 1.5px solid {col};
                border-radius: 10px;
            }}
        """)

    def _blink(self):
        if self._health:
            cur = self._dot.styleSheet()
            if "opacity:0.3" in cur:
                self._dot.setStyleSheet(f"background:transparent;color:{C['accent']}")
            else:
                self._dot.setStyleSheet(f"background:transparent;color:{C['accent']};opacity:0.4")


# ── Mode Card ────────────────────────────────────────────────────────────
class ModeCard(QWidget):
    def __init__(self, data, on_switch):
        super().__init__()
        self._m = data
        self._on_switch = on_switch
        self._active = False
        self._switching = False
        self.setFixedSize(140, 90)
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("modecard")
        self.setStyleSheet(f"""
            QWidget#modecard {{
                background: {C['bg1']};
                border: 1.5px solid {C['border']};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        # Riga 1: emoji 16pt + nome bold 11pt + spacer + pulsante 70x26
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

        # Riga 2: exec text 8pt faint, 2 righe max
        exec_lbl = QLabel(self._m["exec"])
        exec_lbl.setFont(QFont("Sans", 8))
        exec_lbl.setWordWrap(True)
        exec_lbl.setMinimumHeight(28)
        exec_lbl.setMaximumHeight(32)
        exec_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        exec_lbl.setStyleSheet(f"background:transparent;color:{C['faint']}")
        layout.addWidget(exec_lbl, 1)

        # Riga 3: pulsante 70x26 allineato a destra
        self._btn = QPushButton("ON")
        self._btn.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setFixedSize(70, 26)
        self._btn.clicked.connect(lambda: self._on_switch(self._m["id"]))
        layout.addWidget(self._btn, 0, Qt.AlignRight)
        self._update_style()

    def _update_style(self):
        if self._active:
            self._btn.setText("ON")
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['green_btn']};
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
            """)
            self.setStyleSheet(f"""
                QWidget#modecard {{
                    background: {C['accent_bg']};
                    border: 1.5px solid {C['green_btn']};
                    border-radius: 10px;
                }}
            """)
        else:
            self._btn.setText("ON")
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['gray_btn']};
                    color: {C['muted']};
                    border: 1px solid {C['border']};
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    border-color: {C['blue']};
                    color: {C['txt']};
                }}
            """)
            self.setStyleSheet(f"""
                QWidget#modecard {{
                    background: {C['bg1']};
                    border: 1.5px solid {C['border']};
                    border-radius: 10px;
                }}
            """)

    def set_active(self, active):
        self._active = active
        self._update_style()


# ── Main Card ─────────────────────────────────────────────────────────────
class Card(QWidget):
    W = 480
    H = 580

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("AI Router")
        self.setFixedSize(self.W, self.H)
        self._drag = QPoint()
        self._dragging = False
        self._current = "?"
        self._health_ok = False

        # Shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 110))

        container = QWidget(self)
        container.setObjectName("container")
        container.setGraphicsEffect(shadow)
        container.setStyleSheet(f"""
            QWidget#container {{
                background: {C['bg2']};
                border: 1px solid {C['border']};
                border-radius: 16px;
            }}
        """)

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
        body_layout.setSpacing(8)

        # Hero
        self._hero = HeroWidget(self)
        body_layout.addWidget(self._hero)

        # Spacer 12px
        spacer_hero = QWidget()
        spacer_hero.setFixedHeight(12)
        body_layout.addWidget(spacer_hero)

        # ── Sezione ANTHROPIC ────────────────────────────────────────────
        anthropic_label = QLabel("ANTHROPIC")
        anthropic_label.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        anthropic_label.setStyleSheet("background:transparent;color:#5a6470")
        body_layout.addWidget(anthropic_label)

        anthropic_grid = QGridLayout()
        anthropic_grid.setSpacing(8)
        anthropic_modes = ["anthropic"]
        self._cards = {}
        for i, mid in enumerate(anthropic_modes):
            m = next(x for x in MODES if x["id"] == mid)
            card = ModeCard(m, self._do_switch)
            self._cards[mid] = card
            anthropic_grid.addWidget(card, 0, i)
        body_layout.addLayout(anthropic_grid)

        # Spacer 10px
        spacer1 = QWidget()
        spacer1.setFixedHeight(10)
        body_layout.addWidget(spacer1)

        # ── Sezione MINIMAX ──────────────────────────────────────────────
        minimax_label = QLabel("MINIMAX")
        minimax_label.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        minimax_label.setStyleSheet("background:transparent;color:#5a6470")
        body_layout.addWidget(minimax_label)

        minimax_grid = QGridLayout()
        minimax_grid.setSpacing(8)
        minimax_modes = ["minimax"]
        for i, mid in enumerate(minimax_modes):
            m = next(x for x in MODES if x["id"] == mid)
            card = ModeCard(m, self._do_switch)
            self._cards[mid] = card
            minimax_grid.addWidget(card, 0, i)
        body_layout.addLayout(minimax_grid)

        # Spacer 10px
        spacer2 = QWidget()
        spacer2.setFixedHeight(10)
        body_layout.addWidget(spacer2)

        # ── Sezione GLM / Z.AI ──────────────────────────────────────────
        glm_label = QLabel("GLM / Z.AI")
        glm_label.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        glm_label.setStyleSheet("background:transparent;color:#5a6470")
        body_layout.addWidget(glm_label)

        glm_grid = QGridLayout()
        glm_grid.setSpacing(8)
        glm_modes = ["glm", "glm-minimax", "anthropic-glm"]
        for i, mid in enumerate(glm_modes):
            m = next(x for x in MODES if x["id"] == mid)
            card = ModeCard(m, self._do_switch)
            self._cards[mid] = card
            glm_grid.addWidget(card, 0, i)
        body_layout.addLayout(glm_grid)

        # Spacer 10px
        spacer3 = QWidget()
        spacer3.setFixedHeight(10)
        body_layout.addWidget(spacer3)

        # ── Sezione MIX ──────────────────────────────────────────────────
        mix_label = QLabel("MIX")
        mix_label.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        mix_label.setStyleSheet("background:transparent;color:#5a6470")
        body_layout.addWidget(mix_label)

        mix_grid = QGridLayout()
        mix_grid.setSpacing(8)
        mix_modes = ["mixed", "inverse"]
        for i, mid in enumerate(mix_modes):
            m = next(x for x in MODES if x["id"] == mid)
            card = ModeCard(m, self._do_switch)
            self._cards[mid] = card
            mix_grid.addWidget(card, 0, i)
        body_layout.addLayout(mix_grid)

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
            self.move(r.center().x() - self.W // 2, r.center().y() - self.H // 2)

    def _refresh(self):
        self._current = get_current()
        j = http_get(f"{ROUTER}/health")
        self._health_ok = bool(j and j.get("ok"))
        self._update_ui()

    def _update_ui(self):
        mi = next((m for m in MODES if m["id"] == self._current), None)
        icon = mi["icon"] if mi else ""
        mode_text = f"{icon}  {self._current.upper()}" if self._current else "?"
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
            subprocess.run(["systemctl", "--user", "restart", "ai-router"],
                          capture_output=True, timeout=10)
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

    def mouseReleaseEvent(self, _):
        self._dragging = False

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.close()


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
