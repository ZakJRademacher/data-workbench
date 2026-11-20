"""Draw packaging/workbench.ico, the window, taskbar and shortcut icon (needs Pillow)."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
S = 1024  # drawn large, then downsampled for each icon size


def draw():
    image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill="#0E1A2B")
    # Three stacked sheets: the source files a project brings together.
    for i, (fill, edge) in enumerate((("#1B385B", "#375A87"), ("#152942", "#5483C5"), ("#1E4370", "#72A1E9"))):
        y = 650 - i * 120
        top = [(512, y - 170), (800, y - 40), (512, y + 90), (224, y - 40)]
        d.polygon(top, fill=fill, outline=edge, width=22)
    # The checked result.
    d.line([(392, 500), (482, 580), (650, 390)], fill="#FFAA6B", width=64, joint="curve")
    for x, y in ((392, 500), (650, 390)):
        d.ellipse((x - 32, y - 32, x + 32, y + 32), fill="#FFAA6B")
    return image


if __name__ == "__main__":
    out = ROOT / "packaging" / "workbench.ico"
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
