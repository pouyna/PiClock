"""Encryption for the secrets in config.ini: mail passwords and calendar links.

A value that starts with "enc:" is a Fernet token (AES-128-CBC with an HMAC) made with
a key that lives outside the project folder, in ~/.config/piclock/secret.key, owner-only.
app.py encrypts any plain secret it finds in config.ini when it starts, so passwords
typed with `piclock config` or pasted from a PC don't stay readable for long.

What this protects against: anyone who reads or copies config.ini (a backup, a shared
screenshot, a copied project folder) sees tokens, not passwords. What it does not
protect against: someone with root on the Pi or with the SD card in hand can read
the key file as well. A secret on the same disk as its key is only ever that safe.
"""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:"
KEY_FILE = Path(os.environ.get("PICLOCK_KEY_FILE", Path.home() / ".config" / "piclock" / "secret.key"))


class VaultError(Exception):
    """The value can't be decrypted, usually because the key file changed or was lost."""


def is_encrypted(value):
    return str(value or "").strip().startswith(PREFIX)


def _fernet(create):
    try:
        key = KEY_FILE.read_bytes().strip()
    except FileNotFoundError:
        if not create:
            raise VaultError(f"The key file {KEY_FILE} is missing.")
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            KEY_FILE.parent.chmod(0o700)
        except OSError:
            pass
        key = Fernet.generate_key()
        tmp = KEY_FILE.with_suffix(".tmp")
        tmp.write_bytes(key + b"\n")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp, KEY_FILE)
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise VaultError(f"The key file {KEY_FILE} is damaged.")


def encrypt(text):
    """Plain text in, "enc:..." out. Already-encrypted values are returned unchanged."""
    text = str(text if text is not None else "")
    if is_encrypted(text) or text == "":
        return text
    return PREFIX + _fernet(create=True).encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(value):
    """"enc:..." in, plain text out. Plain values pass through, so old config files keep working."""
    value = str(value if value is not None else "").strip()
    if not is_encrypted(value):
        return value
    try:
        return _fernet(create=False).decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise VaultError("This value was encrypted with a different key file, so it can't be read any more.")
