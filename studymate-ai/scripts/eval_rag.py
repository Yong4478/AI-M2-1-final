"""RAG 검색 품질 평가 (Hit@k, MRR).

청크 크기·겹침·top_k 설정을 바꿔 가며 검색 품질을 숫자로 비교한다. (실제 OpenAI 임베딩 API 사용)

사용 예:
    python scripts/eval_rag.py
    python scripts/eval_rag.py --chunk-size 500 --overlap 100
    python scripts/eval_rag.py --document 내강의.pdf --eval-set 내질문.json
    python scripts/eval_rag.py --fake        # API 없이 동작만 확인 (점수는 의미 없음)

평가 세트 형식(JSON): [{"question": "...", "expected": "정답 청크에 들어 있는 문구"}, ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studymate import rag  # noqa: E402
from studymate.auth import register  # noqa: E402
from studymate.config import load_settings  # noqa: E402
from studymate.db import Database  # noqa: E402
from studymate.llm import LLMClient, normalize_rows  # noqa: E402


class FakeEmbedder:
    """API 없이 스크립트 동작만 확인하기 위한 글자 2-gram 해시 임베딩."""

    def embed(self, texts):
        vectors = np.zeros((len(texts), 256), dtype=np.float32)
        for row, text in enumerate(texts):
            compact = re.sub(r"\s+", "", text.lower())
            for i in range(len(compact) - 1):
                vectors[row, int(hashlib.md5(compact[i:i + 2].encode()).hexdigest(), 16) % 256] += 1
        return normalize_rows(vectors)


def evaluate(document: Path, eval_set: Path, chunk_size: int, overlap: int, top_k: int, fake: bool) -> dict:
    db = Database("sqlite:///:memory:")
    db.init_schema()
    user = register(db, "평가용", "0000", consent=True)
    llm = FakeEmbedder() if fake else LLMClient(load_settings())
    info = rag.ingest_document(
        db, llm, user.id, document.name, document.read_bytes(),
        max_chunks=100_000, chunk_size=chunk_size, overlap=overlap,
    )
    cases = json.loads(eval_set.read_text(encoding="utf-8"))
    rows, hit_count, reciprocal = [], 0, 0.0
    for case in cases:
        hits = rag.search(db, llm, user.id, case["question"], top_k=top_k, min_score=0.0)
        rank = next((i + 1 for i, h in enumerate(hits) if case["expected"] in h.content), None)
        hit_count += rank is not None
        reciprocal += 1 / rank if rank else 0
        rows.append((case["question"], rank, hits[0].score if hits else 0.0))
    return {
        "chunks": info.num_chunks,
        "rows": rows,
        "hit_at_k": hit_count / len(cases),
        "mrr": reciprocal / len(cases),
        "n": len(cases),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 검색 품질 평가")
    parser.add_argument("--document", default=str(ROOT / "sample_data" / "sample_ai_basics.md"))
    parser.add_argument("--eval-set", default=str(ROOT / "scripts" / "rag_eval_set.json"))
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--fake", action="store_true", help="API 없이 실행 (동작 확인용)")
    args = parser.parse_args()

    result = evaluate(Path(args.document), Path(args.eval_set), args.chunk_size, args.overlap, args.top_k, args.fake)
    print(f"\n문서: {Path(args.document).name} | 청크 {args.chunk_size}자 / 겹침 {args.overlap}자 → {result['chunks']}개")
    print("-" * 72)
    for question, rank, score in result["rows"]:
        mark = f"{rank}위" if rank else "실패"
        print(f"[{mark:>3}] (1위 유사도 {score:.3f}) {question}")
    print("-" * 72)
    print(f"Hit@{args.top_k}: {result['hit_at_k']:.0%}  |  MRR: {result['mrr']:.3f}  |  질문 {result['n']}개")
    if result["chunks"] <= args.top_k * 3:
        print(f"※ 청크가 {result['chunks']}개뿐이라 변별력이 낮습니다. 실제 강의 PDF처럼 긴 자료와 질문 세트로 비교하세요.")
    if args.fake:
        print("※ --fake 모드는 가짜 임베딩이라 점수는 의미가 없습니다.")


if __name__ == "__main__":
    main()
