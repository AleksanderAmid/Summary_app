"""Exact recognition of visually reviewed, non-text Word document symbols.

Fingerprints cover dimensions and every displayed RGB pixel on a white background.
They survive lossless re-encoding, but do not use fuzzy matching or size-based
omission. Unknown images, cropped logos with lettering, and stamps still need OCR.
Descriptions record the visible glyph only, never its clinical meaning.
"""
import hashlib
import io

# These generic symbols were visually reviewed in an exported Word journal.
# No document text, patient identifiers, filenames, or OCR guesses are stored here.
REVIEWED_SYMBOLS = {
    ((18, 18), "5dd5f8e3e74ec2165c529c6d5fda689b0cd4f90841f883e28352a990f7d89eff"):
        ("pencil", "[Symbol: penna]"),
    ((22, 22), "b70819766636496b7fa7176b587170e662cf2443beed8ff8f6f41847b341fb72"):
        ("green_l", "[Symbol: L i grön cirkel]"),
    ((20, 20), "9fd5b8e0c7adc1eed4515944119734816455eb3a2b55bd16a8776360edb1f1f4"):
        ("green_l", "[Symbol: L i grön cirkel]"),
    ((60, 60), "b24036a5801d5c1ae100bc5de24d3e943ac64ae6feb1ae1881b27b2cc0451a52"):
        ("red_exclamation", "[Symbol: utropstecken i röd cirkel]"),
    ((21, 23), "98ad7bb39f0c7c6914cf483c73621d33c08f9e53940907c05938112c1a59f304"):
        ("blue_i", "[Symbol: i i blå cirkel]"),
    ((102, 99), "e23e3afc01f921acb440921b4c0c88b04746245ef44bc23630443ac80c77893a"):
        ("gray_cross", "[Symbol: grått kors]"),
    ((101, 102), "f064a645d0736360b25f3631f93b8eccd9f728146167f4a0161edcae8c05a40a"):
        ("gray_cross", "[Symbol: grått kors]"),
}
_SIZES = {size for size, _ in REVIEWED_SYMBOLS}


def recognize_symbol(data):
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as original:
        # This gate avoids decoding large scans; size alone never accepts an image.
        if original.size not in _SIZES or getattr(original, "n_frames", 1) != 1:
            return None
        rgba = ImageOps.exif_transpose(original).convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        visible = white.convert("RGB")
        digest = hashlib.sha256(f"{visible.width}x{visible.height}:".encode() + visible.tobytes()).hexdigest()
        match = REVIEWED_SYMBOLS.get((visible.size, digest))
    if match is None:
        return None
    symbol_id, description = match
    return {"text": description, "method": "embedded_symbol", "symbol_id": symbol_id,
            "recognition": "exact_pixels", "warnings": [], "review_required": False,
            "attempts": 0, "seconds": 0, "engines": []}
