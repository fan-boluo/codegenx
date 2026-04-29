import os

def get_env(name: str, default: str = '') -> str:
    return os.getenv(name, default)


def build_service_url(host: str, port: int, path: str) -> str:
    return f'http://{host}:{port}/{path.lstrip('/')}'


import hashlib

from shared.constants import PASSWORD_SALT

# 密码加盐
def encrypt_password(password: str) -> str:
    return hashlib.md5(f"{password}{PASSWORD_SALT}".encode("utf-8")).hexdigest()
