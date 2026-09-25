from __future__ import annotations

from dataclasses import replace
import shutil

from langchain_core.language_models.fake_chat_models import FakeListChatModel
import pytest

from conftest import PROJECT_DIR, RUN_DATE
from core.config import load_settings, normalized_provider, require_llm_credentials
from core.utils import read_json
from evaluation import metrics
from evaluation.metrics import JudgeVerdict, evaluate_pipeline
from evaluation.testset import build_test_set
from ingestion.cleaning import build_clean_dataframe
from ingestion.crossref import load_raw_records
from retrieval import agent as agent_module
from retrieval import qa
from retrieval.index import LocalEmbeddingIndex
from retrieval.llm import build_llm
from retrieval.qa import NO_ANSWER, answer_question, build_qa_prompt


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build the Chroma index once per module (loading MiniLM is the slow part)."""
    root = tmp_path_factory.mktemp("project")
    (root / "data" / "raw").mkdir(parents=True)
    shutil.copy(PROJECT_DIR / "data" / "raw" / "crossref_records.json", root / "data" / "raw")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LLM_PROVIDER", "mock")
        settings = load_settings(root)
    df = build_clean_dataframe(load_raw_records(settings.paths.raw_records_json), RUN_DATE)
    index = LocalEmbeddingIndex.build(df, settings, settings.paths.embeddings_json)
    test_set = build_test_set(df, settings.paths.eval_testset)
    return settings, df, index, test_set


# ---------- index ----------


def test_index_holds_all_papers_with_metadata(built):
    settings, df, index, _ = built
    assert index.collection_name == "papers-baseline"
    assert index.collection.count() == 24
    manifest = read_json(settings.paths.embeddings_json)
    assert manifest["persist_path"] == "data/chroma"  # relative, portable
    assert manifest["embedding_model"].endswith("all-MiniLM-L6-v2")


def test_search_ranks_the_right_paper_first(built):
    _, df, index, _ = built
    row = df.iloc[3]
    results = index.search(row["summary"], top_k=3)
    assert len(results) == 3
    assert results[0].paper_id == row["paper_id"]
    assert 0.0 <= results[-1].score <= results[0].score <= 1.0


def test_lookup_by_id_and_title_is_case_insensitive(built):
    _, df, index, _ = built
    row = df.iloc[0]
    assert index.lookup(row["paper_id"].upper())["title"] == row["title"]
    assert index.lookup(f"  {row['title'].lower()} ")["paper_id"] == row["paper_id"]
    assert index.lookup("no such paper") is None


def test_load_reopens_persisted_index(built):
    settings, _, index, _ = built
    reloaded = LocalEmbeddingIndex.load(settings)
    assert reloaded.collection.count() == index.collection.count()


def test_rebuild_replaces_collection_and_prunes_orphans(built):
    settings, df, _, _ = built
    orphan = settings.paths.chroma_dir / "00000000-0000-4000-8000-000000000000"
    orphan.mkdir()
    (orphan / "data_level0.bin").write_bytes(b"dead")
    corrupted = LocalEmbeddingIndex.build(df.head(10), settings, settings.paths.corrupted_embeddings_json)
    rebuilt = LocalEmbeddingIndex.build(df.head(12), settings, settings.paths.corrupted_embeddings_json)
    assert corrupted.collection_name == rebuilt.collection_name == "papers-corrupted"
    assert rebuilt.collection.count() == 12
    assert not orphan.exists()  # dead segment folders from earlier runs are removed
    custom = LocalEmbeddingIndex._derive_collection_name(settings, settings.paths.project_dir / "My Emb.json")
    assert custom == "my-emb"


# ---------- QA ----------


def test_extractive_answers_cover_all_question_types(built):
    settings, _, index, test_set = built
    for item in test_set:
        result = answer_question(item["question"], settings, index)
        assert result.answer_mode == "extractive"
        assert result.answer == item["ground_truth"]
        assert result.retrieved_doc_ids[0] == item["ground_truth_doc_ids"][0]


def test_llm_mode_uses_grounded_prompt(built, monkeypatch):
    settings, _, index, test_set = built
    seen = {}

    class FakeLLM:
        def invoke(self, prompt):
            seen["prompt"] = prompt
            return FakeListChatModel(responses=[' "2026-07-22" ']).invoke("x")

    monkeypatch.setenv("QA_MODE", "llm")
    monkeypatch.setattr("retrieval.llm.build_llm", lambda settings, temperature: FakeLLM())
    result = answer_question(test_set[2]["question"], settings, index)
    assert result.answer_mode == "llm" and result.answer == "2026-07-22"
    assert "ONLY the context" in seen["prompt"] and NO_ANSWER in seen["prompt"]


def test_llm_mode_falls_back_to_extractive_on_error(built, monkeypatch):
    settings, _, index, test_set = built
    monkeypatch.setenv("QA_MODE", "llm")
    monkeypatch.setattr(qa, "_generate_answer", lambda *a: (_ for _ in ()).throw(RuntimeError("429")))
    result = answer_question(test_set[0]["question"], settings, index)
    assert result.answer_mode == "extractive_fallback"
    assert result.answer == test_set[0]["ground_truth"]


def test_llm_mode_rejects_empty_answer(built, monkeypatch):
    settings, _, index, _ = built
    monkeypatch.setattr("retrieval.llm.build_llm", lambda settings, temperature: FakeListChatModel(responses=[" "]))
    with pytest.raises(ValueError):
        qa._generate_answer("q", index.search("rag"), settings)


def test_invalid_qa_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("QA_MODE", "magic")
    with pytest.raises(ValueError):
        qa.qa_mode()


def test_empty_retrieval_answers_no_answer(built, monkeypatch):
    settings, _, index, _ = built
    monkeypatch.setattr(index, "search", lambda query, top_k=None: [])
    assert answer_question("Anything?", settings, index).answer == NO_ANSWER
    assert "[1]" not in build_qa_prompt("q", [])


# ---------- LLM providers & agent ----------


@pytest.mark.parametrize(
    ("provider", "key_field"),
    [
        ("gemini", "google_api_key"),
        ("openai", "openai_api_key"),
        ("anthropic", "anthropic_api_key"),
        ("openrouter", "openrouter_api_key"),
        ("custom", "custom_llm_base_url"),
        ("ollama", None),
        ("mock", None),
    ],
)
def test_build_llm_for_every_provider(built, provider, key_field):
    settings = replace(built[0], llm_provider=provider, model_name="some-model")
    if key_field:
        with pytest.raises(RuntimeError):
            require_llm_credentials(replace(settings, **{key_field: None}))
        settings = replace(settings, **{key_field: "http://localhost:1" if key_field.endswith("url") else "k"})
    assert build_llm(settings) is not None


def test_provider_aliases_and_unknown_provider(built):
    settings = built[0]
    assert normalized_provider(replace(settings, llm_provider="Anthorpic")) == "anthropic"
    assert normalized_provider(replace(settings, llm_provider="custom-llm")) == "custom"
    with pytest.raises(RuntimeError):
        build_llm(replace(settings, llm_provider="unknown"))


def test_agent_tools_and_answer_extraction(built, monkeypatch):
    settings, df, index, _ = built
    captured = {}

    def fake_create_agent(model, tools, system_prompt, name):
        captured["tools"] = {tool.name: tool for tool in tools}
        return "agent"

    monkeypatch.setattr(agent_module, "create_agent", fake_create_agent)
    assert agent_module.build_agent(settings, index) == "agent"
    tools = captured["tools"]
    assert "paper_id:" in tools["semantic_search_papers"].invoke({"query": "agentic RAG", "top_k": 2})
    assert df.iloc[0]["title"] in tools["lookup_paper"].invoke({"paper_id_or_title": df.iloc[0]["paper_id"]})
    assert tools["lookup_paper"].invoke({"paper_id_or_title": "missing"}) == "No exact paper match found."

    class FakeAgent:
        def __init__(self, messages):
            self.messages = messages

        def invoke(self, payload):
            return {"messages": self.messages}

    final = FakeListChatModel(responses=["final answer"]).invoke("x")
    assert agent_module.run_agent_question(FakeAgent([final]), "q") == "final answer"
    assert agent_module.run_agent_question(FakeAgent([]), "q") == ""


# ---------- evaluation ----------


def test_test_set_covers_four_question_types(built):
    _, df, _, test_set = built
    assert len(test_set) == 10
    counts = {t: sum(i["question_type"] == t for i in test_set) for t in ("summary", "authors", "date", "categories")}
    assert counts == {"summary": 3, "authors": 3, "date": 2, "categories": 2}
    assert len({i["ground_truth_doc_ids"][0] for i in test_set}) == 10
    with pytest.raises(ValueError):
        build_test_set(df.head(5), built[0].paths.eval_testset.with_name("small.json"))


def test_token_f1():
    assert metrics._token_f1("a b c", "a b c") == 1.0
    assert metrics._token_f1("a b", "c d") == 0.0
    assert metrics._token_f1("", "a") == 0.0
    assert 0 < metrics._token_f1("a b c d", "a b") < 1


def test_evaluate_with_unavailable_judge_records_the_error(built):
    settings, _, index, _ = built
    out = settings.paths.baseline_metrics
    bundle = evaluate_pipeline(settings, index, settings.paths.eval_testset, out, settings.paths.baseline_answers)
    summary = read_json(out)
    assert summary["retrieval_hit_rate"] == 1.0 and summary["mean_token_f1"] == 1.0
    assert summary["judge_fallback_count"] == 10
    assert "NotImplementedError" in summary["judge_error"]
    assert summary["qa_mode"] == "extractive" and summary["ragas"]["skipped"]
    assert bundle.answers[0]["judge"]["reasoning"].startswith("Fallback heuristic")


def test_evaluate_with_working_llm_judge(built, monkeypatch):
    settings, _, index, _ = built

    class Judge:
        def with_structured_output(self, schema):
            return self

        def invoke(self, prompt):
            return JudgeVerdict(score=4, correct=True, reasoning="ok")

    monkeypatch.setattr(metrics, "build_llm", lambda settings, temperature: Judge())
    summary = evaluate_pipeline(
        settings, index, settings.paths.eval_testset, settings.paths.baseline_metrics, settings.paths.baseline_answers
    ).summary
    assert summary["judge_fallback_count"] == 0 and summary["judge_error"] is None
    assert summary["mean_judge_score"] == 4


def test_judge_rejects_unparseable_output(built, monkeypatch):
    class Broken:
        def with_structured_output(self, schema):
            return self

        def invoke(self, prompt):
            return None

    monkeypatch.setattr(metrics, "build_llm", lambda settings, temperature: Broken())
    verdict, error = metrics._judge_answer(built[0], "q", "a b", "a b")
    assert verdict.score == 5 and "unparseable" in error


def test_ragas_failure_is_reported_not_raised(built, monkeypatch):
    monkeypatch.setenv("RUN_RAGAS", "1")
    monkeypatch.setattr(metrics, "build_llm", lambda settings, temperature: (_ for _ in ()).throw(RuntimeError("no")))
    answers = [{"question": "q", "answer": "a", "ground_truth": "a", "retrieved_contexts": ["a"]}]
    assert "error" in metrics._run_ragas(built[0], answers)
