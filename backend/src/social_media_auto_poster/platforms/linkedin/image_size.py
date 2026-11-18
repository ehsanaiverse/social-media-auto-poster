"""LinkedIn's image size limit, checked from the file header without decoding the image.

LinkedIn accepts images with "less than 36,152,320 pixels". Rather than adding an
imaging library, the size is read from the PNG or JPEG header with the standard
library (``platforms/image_header.py``, D37). An image whose size cannot be read
is refused rather than guessed.
"""

from social_media_auto_poster.platforms.image_header import ImageSizeError, image_size

__all__ = ["MAX_PIXELS", "ImageSizeError", "image_problem", "image_size"]

# LinkedIn: "Images with less than 36,152,320 pixels".
MAX_PIXELS = 36_152_320


def image_problem(data: bytes) -> str | None:
    """Why LinkedIn would not accept this image's size, or None."""
    try:
        width, height = image_size(data)
    except ImageSizeError as error:
        return f"image size could not be read ({error})"
    if width == 0 or height == 0:
        return f"image size is {width} x {height} pixels"
    if width * height >= MAX_PIXELS:
        return (
            f"image is {width} x {height} = {width * height:,} pixels; "
            f"LinkedIn accepts less than {MAX_PIXELS:,}"
        )
    return None
