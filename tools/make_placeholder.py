"""임시 시작 그림(assets/start.png 800×800)·아이콘(assets/dtmc.ico) 만들기 — 사용자 그림을 받으면 교체.
python tools/make_placeholder.py"""
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
app = QGuiApplication(sys.argv)


def draw(p: QPainter, s: float):
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0.0, QColor("#0f1a2e"))
    g.setColorAt(0.55, QColor("#2a1d3f"))
    g.setColorAt(1.0, QColor("#3b1f35"))
    p.fillRect(QRectF(0, 0, s, s), g)
    glow = QRadialGradient(QPointF(s * 0.68, s * 0.42), s * 0.55)
    glow.setColorAt(0.0, QColor(217, 127, 184, 70))
    glow.setColorAt(1.0, QColor(217, 127, 184, 0))
    p.fillRect(QRectF(0, 0, s, s), glow)
    # 소리 물결 3줄
    p.setRenderHint(QPainter.Antialiasing)
    for k, (col, amp, ph) in enumerate(((QColor(74, 143, 224, 150), 0.07, 0.0), (QColor(217, 127, 184, 140), 0.05, 1.3),
                                        (QColor(63, 184, 160, 120), 0.035, 2.4))):
        path = QPainterPath()
        for i in range(0, 201):
            x = s * i / 200
            y = s * (0.47 + 0.02 * k) + s * amp * math.sin(i / 200 * math.pi * 3 + ph) * math.sin(i / 200 * math.pi)
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(col, s * 0.006))
        p.drawPath(path)
    # 아래 건반
    top = s * 0.70
    n = 14
    w = s / n
    p.setPen(QPen(QColor("#1b1b22"), max(1.0, s * 0.003)))
    p.setBrush(QColor("#ecebf0"))
    for i in range(n):
        p.drawRect(QRectF(i * w, top, w, s - top))
    p.setBrush(QColor("#15151b"))
    for i in range(n):
        if i % 7 in (0, 1, 3, 4, 5):
            p.drawRect(QRectF((i + 0.68) * w, top, w * 0.64, (s - top) * 0.6))
    shade = QLinearGradient(0, top, 0, s)
    shade.setColorAt(0.0, QColor(0, 0, 0, 40))
    shade.setColorAt(1.0, QColor(0, 0, 0, 150))
    p.fillRect(QRectF(0, top, s, s - top), shade)


img = QImage(800, 800, QImage.Format_ARGB32)
p = QPainter(img)
draw(p, 800)
p.end()
(ROOT / "assets").mkdir(exist_ok=True)
img.save(str(ROOT / "assets" / "start.png"))

ico = QImage(256, 256, QImage.Format_ARGB32)
ico.fill(Qt.transparent)
p = QPainter(ico)
p.setRenderHint(QPainter.Antialiasing)
clip = QPainterPath()
clip.addRoundedRect(QRectF(8, 8, 240, 240), 44, 44)
p.setClipPath(clip)
p.translate(8, 8)
draw(p, 240)
p.setClipping(False)
p.setPen(QColor("#ffffff"))
f = p.font()
f.setPixelSize(70)
f.setBold(True)
p.setFont(f)
p.drawText(QRectF(0, 18, 240, 110), Qt.AlignCenter, "MC")
p.end()
ok = ico.save(str(ROOT / "assets" / "dtmc.ico"))
ico.save(str(ROOT / "assets" / "dtmc_icon.png"))
print("start.png ok, ico", ok)
