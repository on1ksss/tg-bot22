"""Шифрование секретов пользователей (ссылка календаря, сессия элжура)."""
from cryptography.fernet import Fernet
from .config import SECRET_KEY


def _f() -> Fernet:
    return Fernet(SECRET_KEY.encode())


def enc(text: str) -> str:
    return _f().encrypt(text.encode()).decode()


def dec(token: str) -> str:
    return _f().decrypt(token.encode()).decode()
