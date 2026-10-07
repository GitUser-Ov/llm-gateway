import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings,SettingsConfigDict

BASE_DIR=Path(__file__).resolve().parents[2]

class Upstream(BaseModel):
    """一个上游模型供应商。

    网关的核心抽象：同一个接口背后可以挂多个上游，
    主上游不可用时自动切换到下一个。
    """
    name:str
    api_key:str=""
    base_url:str="https://api.deepseek.com"
    model:str="deepseek-chat"
    timeout:float=30.0
    enabled:bool=True

class Settings(BaseSettings):
    model_config=SettingsConfigDict(
        env_file=BASE_DIR/".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    app_name:str="llm-gateway"
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
    # 流式调用是否向模型索要用量统计（用于 token 计量）
    llm_stream_usage:bool=True
    # 多上游配置：JSON 数组字符串。留空则回退到下面的 deepseek_* 单上游。
    llm_upstreams:str=""

    # 本地开发默认值；生产部署请通过 DATABASE_URL 环境变量覆盖
    database_url:str="postgresql+psycopg://campus:campus_pwd@localhost:5432/campus"
    db_pool_size:int=5
    db_max_overflow:int=10
    
    redis_url:str="redis://localhost:6379/0"
    answer_cache_ttl:int=3600

    rate_limit_per_minute:int=20
    daily_quota:int=300

    # ---------- RAG（私有知识库检索）----------
    rag_enabled:bool=True
    # 向量后端：local（本地 sentence-transformers）| ollama | fake（仅测试）
    embed_backend:str="local"
    # 中文语料实测：bge-small-zh-v1.5 top-1 100%，优于通用英文模型的 57%
    embed_model:str="BAAI/bge-small-zh-v1.5"
    embed_dim:int=512
    embed_timeout_seconds:float=30.0
    ollama_base_url:str="http://127.0.0.1:11434"
    # 检索参数
    rag_top_k:int=4
    rag_min_score:float=0.35
    chunk_size:int=500
    chunk_overlap:int=80
    # 检索到的资料不足时，是否拒答（而不是让模型自由发挥）
    rag_refuse_when_empty:bool=True

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

    @property
    def upstream_list(self)->list[Upstream]:
        """按顺序返回可用上游。

        配了 LLM_UPSTREAMS 就用它（顺序即优先级，第一个是主上游）；
        没配就回退成单上游，行为与旧版本完全一致。
        """
        raw=(self.llm_upstreams or "").strip()
        if raw:
            try:
                items=json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("LLM_UPSTREAMS 必须是合法的 JSON 数组") from exc
            if not isinstance(items,list):
                raise ValueError("LLM_UPSTREAMS 必须是 JSON 数组")
            ups=[
                Upstream(
                    name=str(it.get("name") or f"upstream-{i}"),
                    api_key=it.get("api_key",""),
                    base_url=it.get("base_url",self.deepseek_base_url),
                    model=it.get("model",self.llm_model),
                    timeout=float(it.get("timeout",self.llm_timeout_seconds)),
                    enabled=bool(it.get("enabled",True)),
                )
                for i,it in enumerate(items)
                if isinstance(it,dict)
            ]
            ups=[u for u in ups if u.enabled]
            if ups:
                return ups
        return [
            Upstream(
                name="deepseek",
                api_key=self.deepseek_api_key,
                base_url=self.deepseek_base_url,
                model=self.llm_model,
                timeout=self.llm_timeout_seconds,
            )
        ]

    @property
    def primary_model(self)->str:
        """缓存 key 用哪个模型名：取主上游的模型，保证与实际调用一致。"""
        return self.upstream_list[0].model

@lru_cache
def get_settings()->Settings:
    return Settings()