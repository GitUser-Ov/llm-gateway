from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings,SettingsConfigDict

BASE_DIR=Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config=SettingsConfigDict(
        env_file=BASE_DIR/".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name:str="campus-ai"
    env:str="dev"
    debug:bool=True

    deepseek_api_key:str=""
    deepseek_base_url:str="https://api.deepseek.com"
    llm_model:str="deepseek-chat"
    llm_timeout_seconds:float=30.0

@lru_cache
def get_settings()->Settings:
    return Settings()