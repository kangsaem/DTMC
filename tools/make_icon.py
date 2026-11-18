"""아이콘(assets/dtmc.ico 16~256px · dtmc_icon.png) = 시작 그림(assets/start.png)과 같은 사진. 시작 그림을 바꾸면 다시 실행.
pip install pillow 후: python tools/make_icon.py"""
from pathlib import Path

from PIL import Image

A = Path(__file__).resolve().parents[1] / "assets"
im = Image.open(A / "start.png").convert("RGBA").resize((256, 256), Image.LANCZOS)
im.save(A / "dtmc.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
im.save(A / "dtmc_icon.png")
print("ok")
