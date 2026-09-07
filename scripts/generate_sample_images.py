"""Generates sample_images/ deterministically — no download, no
external source, no licensing questions.

Every image here is procedurally generated from fixed parameters (a
fixed seed for the one pattern that uses randomness), saved as PNG
(lossless — sidesteps JPEG re-encode variance as a determinism risk
entirely, on top of the OpenCV-version pinning in requirements.txt).
Re-running this script always produces byte-identical files, which is
what makes the stress test's determinism assertion something you can
actually rely on end to end, instead of hoping a downloaded photo never
changes.

Run: python scripts/generate_sample_images.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "sample_images"
SIZE = (640, 480)  # (width, height)


def checkerboard(square: int = 20) -> Image.Image:
    w, h = SIZE
    xx, yy = np.meshgrid(np.arange(w), np.arange(h))
    arr = (((xx // square) + (yy // square)) % 2 == 0).astype(np.uint8) * 255
    return Image.fromarray(arr, mode="L").convert("RGB")


def lines(spacing: int = 12) -> Image.Image:
    img = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(img)
    for x in range(0, SIZE[0], spacing):
        draw.line([(x, 0), (x, SIZE[1])], fill="black", width=2)
    for y in range(0, SIZE[1], spacing):
        draw.line([(0, y), (SIZE[0], y)], fill="black", width=2)
    return img


def gradient() -> Image.Image:
    w, h = SIZE
    arr = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
    return Image.fromarray(arr, mode="L").convert("RGB")


def noise(seed: int = 42) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(SIZE[1], SIZE[0]), dtype=np.uint8)
    return Image.fromarray(arr, mode="L").convert("RGB")


def save(img: Image.Image, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    img.save(path)  # PNG — lossless, no quality/compression param affects decoded pixels
    print(f"wrote {path}")


def main() -> None:
    # High-frequency-detail patterns get a sharp version and a
    # gaussian-blurred counterpart, so each pair straddles the
    # BLUR_THRESHOLD=100.0 cutoff from opposite sides on purpose.
    patterns = {
        "checkerboard": checkerboard(),
        "lines": lines(),
        "noise": noise(),
    }
    for name, img in patterns.items():
        save(img, f"{name}_sharp.png")
        save(img.filter(ImageFilter.GaussianBlur(radius=8)), f"{name}_blurry.png")

    # A naturally low-detail image — classified blurry from having no
    # fine detail to begin with, not from a blur filter. Exercises the
    # same code path a genuinely smooth/out-of-focus photo would.
    save(gradient(), "gradient_smooth.png")

    # A mild blur, closer to the threshold than the radius=8 pairs
    # above — useful for sanity-checking the threshold is actually
    # being applied, not just eyeballed.
    save(checkerboard().filter(ImageFilter.GaussianBlur(radius=3)), "checkerboard_slightly_blurry.png")

    count = len(list(OUT_DIR.glob("*.png")))
    print(f"\n{count} images written to {OUT_DIR}")


if __name__ == "__main__":
    main()
