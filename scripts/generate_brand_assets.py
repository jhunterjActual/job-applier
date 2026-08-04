"""Generate raster favicon and mobile assets from the Job Applier icon geometry."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "backend" / "static" / "icons"
SCALE = 4
CANVAS = 512


def scaled(points):
    return tuple(round(value * SCALE) for value in points)


def create_icon() -> Image.Image:
    size = CANVAS * SCALE
    image = Image.new("RGBA", (size, size), (17, 28, 51, 255))
    draw = ImageDraw.Draw(image)

    for y in range(32 * SCALE, (CANVAS - 32) * SCALE):
        ratio = (y - 32 * SCALE) / ((CANVAS - 64) * SCALE)
        color = (
            round(56 + (168 - 56) * ratio),
            round(189 + (85 - 189) * ratio),
            round(248 + (247 - 248) * ratio),
            255,
        )
        draw.line((32 * SCALE, y, (CANVAS - 32) * SCALE, y), fill=color, width=1)

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(scaled((32, 32, 480, 480)), radius=96 * SCALE, fill=255)
    image.putalpha(mask)

    draw = ImageDraw.Draw(image)
    page = scaled((144, 96, 384, 416))
    draw.rounded_rectangle(page, radius=18 * SCALE, fill=(248, 250, 252, 255))
    draw.polygon([scaled((304, 96)), scaled((384, 176)), scaled((304, 176))], fill=(196, 181, 253, 255))
    draw.line(
        [scaled((190, 281)), scaled((240, 331)), scaled((338, 219))],
        fill=(37, 99, 235, 255),
        width=34 * SCALE,
        joint="curve",
    )
    for point in (scaled((190, 281)), scaled((240, 331)), scaled((338, 219))):
        radius = 17 * SCALE
        draw.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=(37, 99, 235, 255))

    return image.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    icon = create_icon()
    icon.resize((512, 512), Image.Resampling.LANCZOS).save(ICON_DIR / "icon-512.png", optimize=True)
    icon.resize((192, 192), Image.Resampling.LANCZOS).save(ICON_DIR / "icon-192.png", optimize=True)
    icon.resize((180, 180), Image.Resampling.LANCZOS).save(ICON_DIR / "apple-touch-icon.png", optimize=True)
    icon.save(ICON_DIR / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])


if __name__ == "__main__":
    main()
