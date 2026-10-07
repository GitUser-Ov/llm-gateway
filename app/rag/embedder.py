"""向量化（embedding）的可插拔实现。

设计要点：把「怎么把文本变成向量」抽象成一个接口，因为：
  1. 测试不该依赖 torch —— 用 FakeEmbedder 就能测完整检索链路；
  2. 换模型不该改业务代码 —— 本地 bge / Ollama / 任意 OpenAI 兼容接口都行；
  3. 选型需要验证 —— 我们实测过通用英文模型在中文语料上只有 57%，
     中文专用模型 bge-small-zh 是 100%，这个结论应当固化在默认值里。
"""
import hashlib
import logging
from abc import ABC, abstractmethod

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class Embedder(ABC):
    """把一批文本编码成向量。所有实现都必须返回 L2 归一化后的向量。"""

    dim: int

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """返回 shape=(len(texts), dim) 的归一化向量矩阵。"""

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def name(self) -> str:
        return type(self).__name__


class LocalEmbedder(Embedder):
    """本地 sentence-transformers 模型（默认 bge-small-zh-v1.5）。

    中文语料实测：bge-small-zh-v1.5 top-1 命中率 100%（对比 nomic-embed-text 57%）。
    懒加载：第一次真正要用的时候才把模型读进内存。
    """

    def __init__(self, model_name: str, dim: int) -> None:
        self.model_name = model_name
        self._dim = dim
        self._model = None

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"local:{self.model_name}"

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("加载本地向量模型 name=%s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
            # 以真实模型输出为准，避免配置写错维度导致建表错误
            # 新版 API 改名为 get_embedding_dimension，这里兼容两种
            getter = getattr(self._model, "get_embedding_dimension", None) \
                or getattr(self._model, "get_sentence_embedding_dimension")
            self._dim = getter()
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype="float32")


class OllamaEmbedder(Embedder):
    """通过 Ollama 的 /api/embed 做向量化。

    适合已经把向量模型托管在 Ollama 里的场景：进程内不需要 torch。
    """

    def __init__(self, base_url: str, model: str, dim: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def encode(self, texts: list[str]) -> np.ndarray:
        import json
        import urllib.request

        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=settings.embed_timeout_seconds) as resp:
            data = json.loads(resp.read())
        vecs = np.asarray(data["embeddings"], dtype="float32")
        self._dim = vecs.shape[1]
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-12)


class FakeEmbedder(Embedder):
    """确定性假向量：同样的文本永远得到同样的向量。

    只用于测试和离线演示——它没有任何语义能力，检索结果只反映 token 重叠，
    但足以验证「切片 -> 存储 -> 检索 -> 拼上下文」整条链路是否正确。
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self._dim), dtype="float32")
        for i, text in enumerate(texts):
            for token in (text or "").lower().split() or [""]:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                for j in range(0, min(len(digest), self._dim * 2), 2):
                    out[i][(j // 2) % self._dim] += digest[j] / 255.0
            # 也把整串的哈希打进去，避免空文本得到全零向量
            digest = hashlib.sha256((text or "").encode("utf-8")).digest()
            for j in range(min(len(digest), self._dim)):
                out[i][j] += digest[j] / 2550.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-12)


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """按配置返回当前 embedder（单例，避免重复加载模型）。"""
    global _embedder
    if _embedder is None:
        backend = settings.embed_backend.lower()
        if backend == "ollama":
            _embedder = OllamaEmbedder(
                settings.ollama_base_url, settings.embed_model, settings.embed_dim
            )
        elif backend == "fake":
            _embedder = FakeEmbedder(settings.embed_dim)
        else:
            _embedder = LocalEmbedder(settings.embed_model, settings.embed_dim)
        logger.info("向量后端已初始化 backend=%s dim=%s", _embedder.name, _embedder.dim)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """测试用：替换/重置全局 embedder。"""
    global _embedder
    _embedder = embedder
