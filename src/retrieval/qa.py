from __future__ import annotations

from dataclasses import dataclass
import os
import re

from core.config import Settings
from core.utils import first_sentence, normalize_whitespace
from retrieval.index import LocalEmbeddingIndex, SearchResult

NO_ANSWER = "I don't know from the indexed corpus."
QA_MODES = {"extractive", "llm"}

QA_PROMPT = """You answer questions about scholarly papers using ONLY the context below.

Rules:
- Use only facts written in the context. Never use outside knowledge.
- If the context does not contain the paper or the answer, reply exactly: {no_answer}
- Authors question: list the author names exactly as written, separated by ", ".
- Date question: reply with the published date only, in YYYY-MM-DD format.
- Categories question: list the categories exactly as written, separated by ", ".
- Summary question: copy the first sentence of that paper's Summary verbatim.
- Reply with the answer only: no preamble, no quotes, no explanation.

Context:
{context}

Question: {question}
Answer:"""


@dataclass(frozen=True)
class AnswerResult:
    question: str
    answer: str
    retrieved_doc_ids: list[str]
    retrieved_contexts: list[str]
    retrieved_titles: list[str]
    answer_mode: str = "extractive"


def qa_mode() -> str:
    """`extractive` (default, deterministic) or `llm` (grounded generation with the configured provider)."""
    mode = os.getenv("QA_MODE", "extractive").strip().lower()
    if mode not in QA_MODES:
        raise ValueError(f"Unsupported QA_MODE={mode!r}. Expected one of: {sorted(QA_MODES)}.")
    return mode


def _extract_answer(question: str, top_result: SearchResult) -> str:
    lowered = question.lower()
    metadata = top_result.metadata
    if "who authored" in lowered or "list the authors" in lowered:
        return metadata["authors_joined"]
    if "when was" in lowered or "publication date" in lowered or "published on" in lowered:
        return metadata["published"]
    if "what categories" in lowered:
        return metadata["categories_joined"]
    return first_sentence(metadata["summary"])


def build_qa_prompt(question: str, retrieved: list[SearchResult]) -> str:
    context = "\n\n".join(f"[{i}] paper_id: {item.paper_id}\n{item.content}" for i, item in enumerate(retrieved, start=1))
    return QA_PROMPT.format(no_answer=NO_ANSWER, context=context, question=question)


def _generate_answer(question: str, retrieved: list[SearchResult], settings: Settings) -> str:
    from retrieval.llm import build_llm

    response = build_llm(settings=settings, temperature=0.0).invoke(build_qa_prompt(question, retrieved))
    answer = normalize_whitespace(response.text).strip("\"'")
    if not answer:
        raise ValueError("LLM returned an empty answer.")
    return answer


def answer_question(question: str, settings: Settings, index: LocalEmbeddingIndex, top_k: int | None = None) -> AnswerResult:
    title_match = re.search(r"'([^']+)'", question)
    exact = index.lookup(title_match.group(1)) if title_match else None
    retrieved = index.search(question, top_k=top_k)
    if exact:
        exact_result = SearchResult(
            paper_id=exact["paper_id"],
            title=exact["title"],
            score=1.0,
            content=exact["content"],
            metadata=exact["metadata"],
        )
        deduped = [exact_result] + [item for item in retrieved if item.paper_id != exact_result.paper_id]
        retrieved = deduped[: (top_k or settings.top_k)]

    mode = "extractive"
    if not retrieved:
        answer = NO_ANSWER
    elif qa_mode() == "llm":
        try:
            answer = _generate_answer(question, retrieved, settings)
            mode = "llm"
        except Exception as exc:  # quota / network errors must not break the evaluation run
            print(f"[qa] LLM answer failed ({type(exc).__name__}); using extractive answer.")
            answer = _extract_answer(question, retrieved[0])
            mode = "extractive_fallback"
    else:
        answer = _extract_answer(question, retrieved[0])
    return AnswerResult(
        question=question,
        answer=answer,
        retrieved_doc_ids=[item.paper_id for item in retrieved],
        retrieved_contexts=[item.content for item in retrieved],
        retrieved_titles=[item.title for item in retrieved],
        answer_mode=mode,
    )
