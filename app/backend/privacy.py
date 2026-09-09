"""Authenticated, job-scoped encryption for reversible pseudonym mappings."""
import json
from pathlib import Path
import re
from cryptography.fernet import Fernet

PRIVATE_DIR = Path(__file__).resolve().parent.parent / "data" / "private"


class MappingVault:
    """Only ciphertext touches disk. The random key exists in this process only."""
    def __init__(self, job_id, directory=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", job_id):
            raise ValueError("Invalid job ID")
        self.directory = Path(directory or PRIVATE_DIR).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / (job_id + ".enc")
        self._cipher = Fernet(Fernet.generate_key())

    def seal(self, mapping):
        payload = json.dumps(mapping, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(self._cipher.encrypt(payload))

    def restore(self, text):
        mapping = json.loads(self._cipher.decrypt(self.path.read_bytes()))
        inverse = {entry["token"]: original for original, entry in mapping.items()}
        if not inverse:
            return text
        pattern = re.compile("|".join(re.escape(token) for token in sorted(inverse, key=len, reverse=True)))
        return pattern.sub(lambda match: inverse[match.group()], text)

    def close(self):
        # The file is a known direct child, never an arbitrary deletion target.
        if self.path.parent.resolve() != self.directory:
            raise ValueError("Invalid mapping path")
        self.path.unlink(missing_ok=True)
        self._cipher = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
