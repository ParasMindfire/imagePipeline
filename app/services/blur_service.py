"""Blur detection — DESIGN.md "Blur detection & determinism".

Three things kept fixed on purpose, because they're what makes the
result actually deterministic across machines (see DESIGN.md):
grayscale decode with no separate cvtColor step, a rounded variance
(not a raw float), and an exact OpenCV version pin in requirements.txt.
"""
import os

import cv2

RESULT_PRECISION = 4  # decimal places — see DESIGN.md determinism note


class UnreadableImageError(ValueError):
    """Raised when cv2 can't decode the file at all (missing, corrupt,
    not an image, a directory, etc.) — EDGECASE.md 1.3/1.4."""


def analyze_image(path: str, threshold: float, max_size_bytes: int) -> dict:
    # EDGECASE.md 1.3 — cv2.imread on a directory/pipe/device returns
    # None (silent) rather than raising, and a pipe/device can hang the
    # read forever; check isfile()/islink() *before* ever calling
    # cv2.imread so those cases fail fast and cleanly instead. (A
    # symlink pointing outside IMAGE_BASE_DIR is already rejected
    # earlier, by job_service.resolve_image_path — Path.resolve()
    # follows symlinks, so the containment check there catches an
    # escaping symlink's *real* target too; this check catches a
    # symlink to something that's simply not a regular file at all,
    # e.g. a named pipe someone dropped inside the allowed directory.)
    if os.path.islink(path) or not os.path.isfile(path):
        raise UnreadableImageError(f"not a regular file: {path}")

    # EDGECASE.md 1.5 — reject oversized files before decode, not after
    # the worker OOMs on a 500MB TIFF or a decompression-bomb PNG.
    size = os.path.getsize(path)
    if size > max_size_bytes:
        raise UnreadableImageError(f"image too large ({size} bytes > {max_size_bytes} byte limit): {path}")

    # NumPy array is made here . Every number is a pixel intensity. Grayscale is used instead of rgb because blur detection doesnt need color 
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        # cv2.imread fails by returning None, not by raising — EDGECASE.md
        # 1.4 flags this explicitly as the thing to check for (corrupt
        # file, zero-byte file, not actually an image despite the name).
        raise UnreadableImageError(f"unreadable or invalid image: {path}")

    # Compute the variance of the Laplacian (edge detector).
    # Sharp images have many strong edges, producing a wide spread of
    # Laplacian values (high variance). Blurry images have smoother
    # transitions, so Laplacian values are more uniform (low variance).
    variance = float(cv2.Laplacian(img, cv2.CV_64F).var())
    variance = round(variance, RESULT_PRECISION)

    return {"is_blurry": variance < threshold, "sharpness_score": variance}
