from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings,SettingsConfigDict

BASE_DIR=Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config=SettingsConfigDict(
        env_file=BASE_DIR/".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    app_name:str="campus-ai"
    env:str="dev"
    debug:bool=True
    log_level:str="INFO"

    deepseek_api_key:str=""
    deepseek_base_url:str="https://api.deepseek.com"
    llm_model:str="deepseek-chat"
    llm_timeout_seconds:float=30.0
    llm_max_retries:int=2
    llm_retry_base_delay:float=0.5
    llm_fallback_message:str="抱歉，模型服务暂时不可用，请稍后重试"

    database_url:str="postgresql+psycopg://campus:campus_pwd@localhost:5432/campus"
    db_pool_size:int=5
    db_max_overflow:int=10
    
    redis_url:str="redis://localhost:6379/0"
    answer_cache_ttl:int=3600

    rate_limit_per_minute:int=20
    daily_quota:int=300

    cors_origins:str="*"

    @property
    def is_prod(self)->bool:
        return self.env.lower()=="prod"

    @property
    def cors_origin_list(self)->list[str]:
        raw=(self.cors_origins or "").strip()
        if raw in ("","*"):
            return ["*"]
        return [o.strip() for o in raw.split(",")if o.strip()]

@lru_cache
def get_settings()->Settings:
    return Settings()