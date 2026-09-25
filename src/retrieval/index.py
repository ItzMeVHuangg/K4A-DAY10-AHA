from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any

import chromadb
import pandas as pd

from core.config import Settings
from core.utils import read_json, safe_slug, write_json
from retrieval.embeddings import MiniLMEmbeddings

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CHROMA_BATCH_SIZE = 100


@dataclass(frozen=True)
class SearchResult:
    """Standardized retrieval search result object."""

    paper_id: str
    title: str
    score: float
    content: str
    metadata: dict[str, Any]


class LocalEmbeddingIndex:
    """Manages local Chroma vector collection indexing, similarity search, and persistence.

    Features:
        - Portable manifests using relative paths.
        - Ghost vector and orphan segment purging on SQLite/Windows.
        - Idempotent collection recreation.
        - Clamped cosine similarity scoring in [0.0, 1.0].
    """

    def __init__(
        self,
        settings: Settings,
        collection_name: str,
        documents: list[dict[str, Any]],
        persist_path: Path,
    ):
        self.settings = settings
        self.collection_name = collection_name
        self.documents = documents
        self.persist_path = persist_path
        self.embedding_backend = "chroma"
        self.embedding_model = MiniLMEmbeddings(settings.embedding_model)
        self.client = chromadb.PersistentClient(path=str(persist_path))
        self.collection = self.client.get_collection(name=collection_name)
        self.documents_by_paper_id = {document["paper_id"].lower(): document for document in documents}
        self.documents_by_title = {document["title"].lower(): document for document in documents}

    @staticmethod
    def _build_documents(df: pd.DataFrame) -> list[dict[str, Any]]:
        """Construct structured document records and vector search metadata from DataFrame."""
        records = df.to_dict(orient="records")
        documents: list[dict[str, Any]] = []
        for index, row in enumerate(records):
            documents.append(
                {
                    "record_id": f"{row['paper_id']}::{index}",
                    "paper_id": row["paper_id"],
                    "title": row["title"],
                    "content": row["text_for_embedding"],
                    "metadata": {
                        "paper_id": str(row["paper_id"]),
                        "title": str(row["title"]),
                        "published": str(row["published"]),
                        "authors_joined": str(row["authors_joined"]),
                        "categories_joined": str(row["categories_joined"]),
                        "summary": str(row["summary"]),
                        "abs_url": str(row["abs_url"]),
                        "pdf_url": str(row["pdf_url"]),
                    },
                }
            )
        return documents

    @staticmethod
    def _derive_collection_name(settings: Settings, embeddings_output_path: Path | None) -> str:
        """Derive canonical collection name based on destination path mapping."""
        if embeddings_output_path is None:
            return settings.baseline_collection_name

        name_map = {
            settings.paths.embeddings_json.resolve(): settings.baseline_collection_name,
            settings.paths.corrupted_embeddings_json.resolve(): settings.corrupted_collection_name,
            settings.paths.repaired_embeddings_json.resolve(): settings.repaired_collection_name,
        }
        resolved_path = embeddings_output_path.resolve()
        if resolved_path in name_map:
            return name_map[resolved_path]
        return safe_slug(embeddings_output_path.stem)

    @staticmethod
    def _prune_orphan_segments(persist_path: Path) -> None:
        """Purge orphan segment directories of deleted collections to avoid ghost artifacts.

        When Chroma deletes a collection on Windows, open handles can leave lingering directories.
        This inspects SQLite metadata and safely prunes inactive folders.
        """
        database = persist_path / "chroma.sqlite3"
        if not database.exists():
            return
        try:
            with closing(sqlite3.connect(database)) as connection:
                live = {row[0] for row in connection.execute("SELECT id FROM segments")}
        except sqlite3.Error:
            return
        for folder in persist_path.iterdir():
            if folder.is_dir() and UUID_PATTERN.fullmatch(folder.name) and folder.name not in live:
                shutil.rmtree(folder, ignore_errors=True)

    @classmethod
    def build(
        cls,
        df: pd.DataFrame,
        settings: Settings,
        embeddings_output_path: Path | None = None,
    ) -> "LocalEmbeddingIndex":
        """Build Chroma vector index idempotently from clean dataframe."""
        collection_name = cls._derive_collection_name(settings, embeddings_output_path)
        documents = cls._build_documents(df)
        persist_path = settings.paths.chroma_dir
        persist_path.mkdir(parents=True, exist_ok=True)
        cls._prune_orphan_segments(persist_path)

        embedding_model = MiniLMEmbeddings(settings.embedding_model)
        client = chromadb.PersistentClient(path=str(persist_path))
        try:
            client.delete_collection(name=collection_name)
        except Exception:
            pass

        collection = client.create_collection(
            name=collection_name,
            configuration={"hnsw": {"space": "cosine"}},
        )

        contents = [document["content"] for document in documents]
        embeddings = embedding_model.embed_documents(contents)

        # Batch ingestion to guarantee safety under larger document sets
        for i in range(0, len(documents), CHROMA_BATCH_SIZE):
            batch_docs = documents[i : i + CHROMA_BATCH_SIZE]
            batch_embeds = embeddings[i : i + CHROMA_BATCH_SIZE]
            collection.add(
                ids=[doc["record_id"] for doc in batch_docs],
                embeddings=batch_embeds,
                documents=[doc["content"] for doc in batch_docs],
                metadatas=[doc["metadata"] for doc in batch_docs],
            )

        cls._prune_orphan_segments(persist_path)

        manifest_path = embeddings_output_path or settings.paths.embeddings_json
        write_json(
            manifest_path,
            {
                "backend": "chroma",
                "embedding_model": settings.embedding_model,
                # Relative path ensures portability across team environments and grading systems
                "persist_path": persist_path.relative_to(settings.paths.project_dir).as_posix(),
                "collection_name": collection_name,
                "documents": documents,
            },
        )
        return cls(
            settings=settings,
            collection_name=collection_name,
            documents=documents,
            persist_path=persist_path,
        )

    @classmethod
    def load(cls, settings: Settings, embeddings_path: Path | None = None) -> "LocalEmbeddingIndex":
        """Load an existing index from its portable JSON manifest."""
        payload = read_json(embeddings_path or settings.paths.embeddings_json)
        return cls(
            settings=settings,
            collection_name=payload["collection_name"],
            documents=payload["documents"],
            persist_path=settings.paths.project_dir / payload["persist_path"],
        )

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        """Execute semantic cosine similarity search against indexed vector documents."""
        if not query or not query.strip():
            return []

        query_embedding = self.embedding_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k or self.settings.top_k,
            include=["documents", "metadatas", "distances"],
        )
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        scored: list[SearchResult] = []
        for record_id, content, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            if not record_id or not metadata or not content:
                continue
            dist_val = float(distance) if distance is not None else 0.0
            # Strict clamping within [0.0, 1.0]
            similarity = max(0.0, min(1.0, 1.0 - dist_val))
            scored.append(
                SearchResult(
                    paper_id=str(metadata["paper_id"]),
                    title=str(metadata["title"]),
                    score=similarity,
                    content=str(content),
                    metadata=dict(metadata),
                )
            )
        return scored

    def lookup(self, value: str) -> dict[str, Any] | None:
        """Direct O(1) hash lookup by paper DOI identifier or normalized title."""
        needle = value.strip().lower()
        if needle in self.documents_by_paper_id:
            return self.documents_by_paper_id[needle]
        if needle in self.documents_by_title:
            return self.documents_by_title[needle]
        return None
