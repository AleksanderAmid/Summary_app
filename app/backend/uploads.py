"""Validate one pasted text or up to ten documents before starting a job."""
import base64
import binascii
import re
from pathlib import PurePosixPath

from transcription import SUPPORTED_EXTENSIONS

MAX_DOCUMENTS = 10
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # combined decoded size
MAX_REQUEST_BYTES = 4 * ((MAX_UPLOAD_BYTES + 2) // 3) + 1024 * 1024


class UploadError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def parse_payload(body):
    if not isinstance(body, dict):
        raise UploadError("Request must be a JSON object.")
    additional = body.get("additional_identifiers", [])
    if (not isinstance(additional, list) or len(additional) > 50
            or any(not isinstance(item, str) or not 2 <= len(item.strip()) <= 180 for item in additional)):
        raise UploadError("Additional identifiers must be up to 50 entries, 2 to 180 characters each.")
    options = {"additional_identifiers": [item.strip() for item in additional]} if additional else {}
    mode = body.get("transcription_mode", "balanced")
    if not isinstance(mode, str) or mode not in ("balanced", "thorough", "fast"):
        raise UploadError("Choose balanced, thorough or fast transcription.")
    if "transcription_mode" in body:
        options["transcription_mode"] = mode
    if "files" in body:
        files = body["files"]
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_DOCUMENTS:
            raise UploadError("Select between 1 and 10 documents.")
        if any(key in body for key in ("text", "filename", "content_b64")):
            raise UploadError("Send documents or pasted text, not both.")
    elif "filename" in body or "content_b64" in body:
        files = [body]
    else:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise UploadError("Provide documents or pasted text.")
        return {"text": text, **options}

    validated, total = [], 0
    for index, document in enumerate(files, 1):
        if not isinstance(document, dict):
            raise UploadError(f"Document {index} must be an object.")
        filename, encoded = document.get("filename"), document.get("content_b64")
        if not isinstance(filename, str) or not filename.strip():
            raise UploadError(f"Document {index} needs a filename.")
        filename = PurePosixPath(filename.replace("\\", "/")).name
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
        if len(filename) > 180:
            raise UploadError(f"Document {index}: filename is too long (max 180 characters).")
        ext = PurePosixPath(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise UploadError(f"{filename}: unsupported file type. Supported: "
                              + ", ".join(SUPPORTED_EXTENSIONS))
        if not isinstance(encoded, str):
            raise UploadError(f"{filename}: missing file content.")
        if len(encoded) > 4 * ((MAX_UPLOAD_BYTES + 2) // 3):
            raise UploadError("Documents exceed the 64 MB combined limit.", 413)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise UploadError(f"{filename}: invalid base64 content.") from None
        if not content:
            raise UploadError(f"{filename}: the document is empty.")
        total += len(content)
        if total > MAX_UPLOAD_BYTES:
            raise UploadError("Documents exceed the 64 MB combined limit.", 413)
        validated.append({"filename": filename, "content": content})
    return {"files": validated, **options}
