"""文档切片（chunking）。

切片是 RAG 里最容易被低估的一步：切太大，检索到的上下文掺了无关内容、
还浪费 token；切太小，一句话被拦腰截断、语义残缺。

这里采用「按标题分节 + 段落聚合 + 定长滑窗兜底」的三层策略：
  1. 先按 Markdown 标题 / 空行切成"块"，尽量不破坏语义边界；
  2. 把相邻的小块合并到接近目标长度，避免碎片化；
  3. 块内超长时用带重叠的定长窗口切开，保证句子不在边界处丢失。
重叠（overlap）的作用：让跨边界的答案至少在某一块里是完整的。
"""
import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    content: str
    heading: str = ""
    index: int = 0


def split_sections(text: str) -> list[tuple[str, str]]:
    """切成 (标题, 正文) 列表。标题会沿用到下一个标题出现为止。"""
    lines = (text or "").splitlines()
    sections: list[tuple[str, str]] = []
    current_heading = ""
    buffer: list[str] = []

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            sections.append((current_heading, body))

    for line in lines:
        m = HEADING_RE.match(line.strip())
        if m:
            flush()
            buffer = []
            current_heading = m.group(2).strip()
        else:
            buffer.append(line)
    flush()
    return sections


def _window(text: str, size: int, overlap: int) -> list[str]:
    """定长滑窗；按句子边界尽量切得自然一些。"""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []

    # 优先在句末标点处断开
    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text[start:end]
            # 从后往前找一个句子边界
            for sep in ("。", "！", "？", "\n", "；", ".", "!", "?"):
                pos = window.rfind(sep)
                if pos > size * 0.5:
                    end = start + pos + 1
                    break
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return pieces


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 80,
) -> list[Chunk]:
    """把一篇文档切成块。chunk_size / overlap 以字符计（中文按字算）。"""
    if overlap >= chunk_size:
        raise ValueError("overlap 必须小于 chunk_size，否则窗口无法前进")

    chunks: list[Chunk] = []
    for heading, body in split_sections(text):
        # 段落聚合：把短段落合并到接近 chunk_size
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        bucket = ""
        for para in paragraphs:
            # 单段就超长，先冲掉已有内容，再对它单独开窗
            if len(para) > chunk_size:
                if bucket:
                    chunks.append(Chunk(content=bucket.strip(), heading=heading))
                    bucket = ""
                for piece in _window(para, chunk_size, overlap):
                    chunks.append(Chunk(content=piece, heading=heading))
                continue
            if len(bucket) + len(para) + 1 <= chunk_size:
                bucket = f"{bucket}\n{para}" if bucket else para
            else:
                chunks.append(Chunk(content=bucket.strip(), heading=heading))
                bucket = para
        if bucket:
            chunks.append(Chunk(content=bucket.strip(), heading=heading))

    for i, c in enumerate(chunks):
        c.index = i
    return chunks
