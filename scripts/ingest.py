"""知识库灌入脚本：把文档切片、向量化、写进 pgvector。

用法：
    python -m scripts.ingest <文件或目录> [更多路径...] [--title 标题]

支持 .md / .txt；目录会递归收集这两类文件。
以文件路径作为 source，因此同一份文件重复灌入是「替换」而不是「追加」。
"""
import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.db.base import SessionLocal
from app.db.rag_models import Chunk, Document
from app.rag.service import ingest_document

SUPPORTED = {".md", ".txt"}


def collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*") if q.suffix.lower() in SUPPORTED))
        elif p.is_file():
            files.append(p)
        else:
            print(f"  [跳过] 不存在: {p}")
    return files


async def main() -> None:
    parser = argparse.ArgumentParser(description="把文档灌入知识库")
    parser.add_argument("paths", nargs="+", help="文件或目录路径")
    parser.add_argument("--title", default="", help="仅单文件时生效的自定义标题")
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("没有找到可灌入的文件（支持 .md / .txt）")
        sys.exit(1)

    print(f"准备灌入 {len(files)} 个文件")
    print("-" * 62)

    async with SessionLocal() as db:
        for i, path in enumerate(files, start=1):
            text = path.read_text(encoding="utf-8", errors="ignore")
            title = args.title if (args.title and len(files) == 1) else path.stem
            try:
                doc = await ingest_document(
                    db,
                    source=str(path).replace("\\", "/"),
                    text=text,
                    title=title,
                )
                print(f"  [{i}/{len(files)}] ✓ {path.name}  ->  {doc.chunk_count} 个切片")
            except Exception as exc:
                print(f"  [{i}/{len(files)}] ✗ {path.name}  失败: {exc}")

        total_docs = await db.scalar(select(func.count()).select_from(Document))
        total_chunks = await db.scalar(select(func.count()).select_from(Chunk))

    print("-" * 62)
    print(f"知识库现状：{total_docs} 份文档 / {total_chunks} 个切片")


if __name__ == "__main__":
    # Windows 上 psycopg 不能在 ProactorEventLoop 里异步运行，
    # 必须显式指定 SelectorEventLoop（与 app/main.py、create_user.py 一致）
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
