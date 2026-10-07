import hashlib
import secrets

KEY_PREFIX="sk-llm-"

def hash_api_key(raw:str)->str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def generate_api_key()->str:
    return KEY_PREFIX+secrets.token_urlsafe(24)


def mask_api_key(raw:str)->str:
    if not raw or len(raw)<12:
        return "***"
    return f"{raw[:11]}...{raw[-4:]}"

