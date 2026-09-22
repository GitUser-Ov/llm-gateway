import hashlib
import secrets

def hash_api_key(raw:str)->str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def generate_api_key()->str:
    return "sk-campus-"+secrets.token_urlsafe(24)

