from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from support_agent_lab.memory.store import KnowledgeIndex, tokenize
from support_agent_lab.models import RetrievalContext, RetrievalHit, RetrievalTrace


KNOWLEDGE_SCHEMA_VERSION = "sqlite_knowledge.v1"
KNOWLEDGE_SUMMARY_SCHEMA_VERSION = "knowledge_index_summary.v1"
DEFAULT_CHUNK_CHARS = 1200
DEFAULT_CHUNK_OVERLAP_CHARS = 160
SAFE_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+", re.UNICODE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class KnowledgeDocumentInput:
    document_id: str
    title: str
    content: str
    source_uri: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeIngestReport:
    schema_version: str
    tenant_id: str
    source_label: str
    document_count: int
    chunk_count: int
    replaced_document_count: int
    skipped_document_count: int
    content_hashes: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeIndexSummary:
    schema_version: str
    provider: Literal["sqlite"]
    tenant_id: str
    document_count: int
    chunk_count: int
    source_count: int
    last_ingested_at: datetime | None
    last_updated_at: datetime | None
    fts_enabled: bool
    database_file: str | None
    database_path_hash: str | None


class SQLiteKnowledgeIndex:
    """SQLite-backed knowledge index for single-instance production/staging.

    It stores real ingested documents and chunks. It does not depend on bundled
    fixtures, and can be used when a team does not yet run a separate knowledge
    service.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        tenant_id: str,
        fts_enabled: bool = True,
        max_scan_chunks: int = 5000,
    ) -> None:
        self.database_path = Path(database_path)
        self.tenant_id = tenant_id
        self.fts_enabled = fts_enabled
        self.max_scan_chunks = max(100, max_scan_chunks)
        self._rewrite = KnowledgeIndex()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_url(
        cls,
        database_url: str,
        *,
        tenant_id: str,
        fts_enabled: bool = True,
        max_scan_chunks: int = 5000,
    ) -> "SQLiteKnowledgeIndex":
        if not database_url.startswith("sqlite:///"):
            raise RuntimeError("SQLiteKnowledgeIndex requires a sqlite:/// database URL")
        raw_path = database_url.removeprefix("sqlite:///")
        if not raw_path:
            raise RuntimeError("SQLiteKnowledgeIndex requires a sqlite:/// database path")
        return cls(
            raw_path,
            tenant_id=tenant_id,
            fts_enabled=fts_enabled,
            max_scan_chunks=max_scan_chunks,
        )

    def search(
        self,
        query: str,
        limit: int = 4,
        context: RetrievalContext | None = None,
    ) -> RetrievalTrace:
        tenant_id = context.tenant_id if context else self.tenant_id
        limit = max(1, min(limit, 20))
        rewritten = self._rewrite_queries(query)
        candidates = self._candidate_rows(tenant_id=tenant_id, queries=rewritten)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in candidates:
            score = max(self._score(rewritten_query, row["content"], row["title"]) for rewritten_query in rewritten)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
        selected = scored[:limit]
        dropped = [row["chunk_id"] for _, row in scored[limit : limit + 50]]
        hits = [
            RetrievalHit(
                document_id=row["document_id"],
                chunk_id=row["chunk_id"],
                title=row["title"],
                content=row["content"],
                score=round(score, 4),
                source_uri=row["source_uri"],
                metadata=_loads_json(row["metadata_json"]),
            )
            for score, row in selected
        ]
        stages = {
            "sqlite_candidates": len(candidates),
            "lexical_scored": len(scored),
            "selected": len(hits),
        }
        if self.fts_enabled:
            stages["fts_enabled"] = 1
        return RetrievalTrace(
            query=query,
            rewritten_queries=rewritten,
            selected_sources=[hit.source_uri for hit in hits],
            candidates_by_stage=stages,
            selected_context=hits,
            dropped_candidates=dropped,
        )

    def ingest_documents(
        self,
        documents: Iterable[KnowledgeDocumentInput],
        *,
        source_label: str,
        replace_source: bool = False,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    ) -> KnowledgeIngestReport:
        now = _utc_now()
        docs = list(documents)
        warnings: list[str] = []
        if replace_source:
            self._delete_source(source_label)
        document_count = 0
        chunk_count = 0
        replaced_count = 0
        skipped_count = 0
        content_hashes: list[str] = []
        with self._connect() as conn:
            conn.execute("begin")
            for doc in docs:
                normalized = _normalize_document(doc)
                if not normalized.content.strip():
                    skipped_count += 1
                    warnings.append(f"empty_document:{normalized.document_id}")
                    continue
                existing = conn.execute(
                    """
                    select content_hash
                    from knowledge_documents
                    where tenant_id = ? and document_id = ?
                    """,
                    (self.tenant_id, normalized.document_id),
                ).fetchone()
                content_hash = _hash_text(normalized.content)
                if existing and existing["content_hash"] == content_hash:
                    skipped_count += 1
                    content_hashes.append(content_hash)
                    continue
                if existing:
                    replaced_count += 1
                chunks = _chunk_document(
                    normalized,
                    chunk_chars=max(400, chunk_chars),
                    chunk_overlap_chars=max(0, min(chunk_overlap_chars, chunk_chars // 2)),
                )
                metadata = {
                    **normalized.metadata,
                    "source_label": source_label,
                    "ingested_at": now.isoformat(),
                    "content_hash": content_hash,
                }
                conn.execute(
                    """
                    insert into knowledge_documents (
                      tenant_id, document_id, title, source_uri, source_label,
                      content_hash, metadata_json, created_at, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(tenant_id, document_id) do update set
                      title = excluded.title,
                      source_uri = excluded.source_uri,
                      source_label = excluded.source_label,
                      content_hash = excluded.content_hash,
                      metadata_json = excluded.metadata_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        self.tenant_id,
                        normalized.document_id,
                        normalized.title,
                        normalized.source_uri,
                        source_label,
                        content_hash,
                        _dumps_json(metadata),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                conn.execute(
                    "delete from knowledge_chunks where tenant_id = ? and document_id = ?",
                    (self.tenant_id, normalized.document_id),
                )
                if self._fts_available(conn):
                    conn.execute(
                        "delete from knowledge_chunks_fts where tenant_id = ? and document_id = ?",
                        (self.tenant_id, normalized.document_id),
                    )
                for chunk in chunks:
                    conn.execute(
                        """
                        insert into knowledge_chunks (
                          tenant_id, chunk_id, document_id, ordinal, title, content,
                          source_uri, metadata_json, content_hash, token_count,
                          created_at, updated_at
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.tenant_id,
                            chunk["chunk_id"],
                            normalized.document_id,
                            chunk["ordinal"],
                            chunk["title"],
                            chunk["content"],
                            normalized.source_uri,
                            _dumps_json(chunk["metadata"]),
                            chunk["content_hash"],
                            chunk["token_count"],
                            now.isoformat(),
                            now.isoformat(),
                        ),
                    )
                    if self._fts_available(conn):
                        conn.execute(
                            """
                            insert into knowledge_chunks_fts (
                              tenant_id, document_id, chunk_id, title, content, source_uri
                            )
                            values (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                self.tenant_id,
                                normalized.document_id,
                                chunk["chunk_id"],
                                chunk["title"],
                                chunk["content"],
                                normalized.source_uri,
                            ),
                        )
                document_count += 1
                chunk_count += len(chunks)
                content_hashes.append(content_hash)
            conn.execute(
                """
                insert into knowledge_ingest_batches (
                  tenant_id, source_label, document_count, chunk_count,
                  replaced_document_count, skipped_document_count,
                  content_hashes_json, warnings_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.tenant_id,
                    source_label,
                    document_count,
                    chunk_count,
                    replaced_count,
                    skipped_count,
                    _dumps_json(content_hashes),
                    _dumps_json(warnings),
                    now.isoformat(),
                ),
            )
            conn.commit()
        return KnowledgeIngestReport(
            schema_version=KNOWLEDGE_SCHEMA_VERSION,
            tenant_id=self.tenant_id,
            source_label=source_label,
            document_count=document_count,
            chunk_count=chunk_count,
            replaced_document_count=replaced_count,
            skipped_document_count=skipped_count,
            content_hashes=content_hashes,
            warnings=warnings,
        )

    def summary(self) -> KnowledgeIndexSummary:
        with self._connect() as conn:
            row = conn.execute(
                """
                select
                  count(*) as document_count,
                  count(distinct source_label) as source_count,
                  max(updated_at) as last_updated_at
                from knowledge_documents
                where tenant_id = ?
                """,
                (self.tenant_id,),
            ).fetchone()
            chunk_row = conn.execute(
                "select count(*) as chunk_count from knowledge_chunks where tenant_id = ?",
                (self.tenant_id,),
            ).fetchone()
            ingest_row = conn.execute(
                """
                select max(created_at) as last_ingested_at
                from knowledge_ingest_batches
                where tenant_id = ?
                """,
                (self.tenant_id,),
            ).fetchone()
        return KnowledgeIndexSummary(
            schema_version=KNOWLEDGE_SUMMARY_SCHEMA_VERSION,
            provider="sqlite",
            tenant_id=self.tenant_id,
            document_count=int(row["document_count"] or 0),
            chunk_count=int(chunk_row["chunk_count"] or 0),
            source_count=int(row["source_count"] or 0),
            last_ingested_at=_parse_dt(ingest_row["last_ingested_at"]),
            last_updated_at=_parse_dt(row["last_updated_at"]),
            fts_enabled=self.fts_enabled,
            database_file=self.database_path.name,
            database_path_hash=_hash_text(str(self.database_path.resolve())),
        )

    async def health_check(self, *, min_documents: int = 1) -> None:
        summary = self.summary()
        if summary.document_count < min_documents:
            raise RuntimeError(
                f"SQLite knowledge index has {summary.document_count} document(s); "
                f"requires at least {min_documents}"
            )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("pragma journal_mode=wal")
            conn.execute("pragma busy_timeout=5000")
            conn.execute("pragma synchronous=NORMAL")
            conn.executescript(
                """
                create table if not exists knowledge_documents (
                  tenant_id text not null,
                  document_id text not null,
                  title text not null,
                  source_uri text not null,
                  source_label text not null,
                  content_hash text not null,
                  metadata_json text not null,
                  created_at text not null,
                  updated_at text not null,
                  primary key (tenant_id, document_id)
                );

                create table if not exists knowledge_chunks (
                  tenant_id text not null,
                  chunk_id text not null,
                  document_id text not null,
                  ordinal integer not null,
                  title text not null,
                  content text not null,
                  source_uri text not null,
                  metadata_json text not null,
                  content_hash text not null,
                  token_count integer not null,
                  created_at text not null,
                  updated_at text not null,
                  primary key (tenant_id, chunk_id),
                  foreign key (tenant_id, document_id)
                    references knowledge_documents(tenant_id, document_id)
                    on delete cascade
                );

                create table if not exists knowledge_ingest_batches (
                  id integer primary key autoincrement,
                  tenant_id text not null,
                  source_label text not null,
                  document_count integer not null,
                  chunk_count integer not null,
                  replaced_document_count integer not null,
                  skipped_document_count integer not null,
                  content_hashes_json text not null,
                  warnings_json text not null,
                  created_at text not null
                );

                create index if not exists idx_knowledge_documents_tenant_source
                  on knowledge_documents(tenant_id, source_label, updated_at);
                create index if not exists idx_knowledge_chunks_tenant_document
                  on knowledge_chunks(tenant_id, document_id, ordinal);
                create index if not exists idx_knowledge_chunks_tenant_updated
                  on knowledge_chunks(tenant_id, updated_at);
                create index if not exists idx_knowledge_ingest_batches_tenant_created
                  on knowledge_ingest_batches(tenant_id, created_at);
                """
            )
            if self.fts_enabled:
                try:
                    conn.execute(
                        """
                        create virtual table if not exists knowledge_chunks_fts
                        using fts5(
                          tenant_id unindexed,
                          document_id unindexed,
                          chunk_id unindexed,
                          title,
                          content,
                          source_uri unindexed
                        )
                        """
                    )
                except sqlite3.OperationalError:
                    self.fts_enabled = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys=on")
        return conn

    def _delete_source(self, source_label: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "select document_id from knowledge_documents where tenant_id = ? and source_label = ?",
                (self.tenant_id, source_label),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "delete from knowledge_documents where tenant_id = ? and document_id = ?",
                    (self.tenant_id, row["document_id"]),
                )
                if self._fts_available(conn):
                    conn.execute(
                        "delete from knowledge_chunks_fts where tenant_id = ? and document_id = ?",
                        (self.tenant_id, row["document_id"]),
                    )

    def _candidate_rows(self, *, tenant_id: str, queries: list[str]) -> list[sqlite3.Row]:
        seen: set[str] = set()
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            if self._fts_available(conn):
                for rewritten in queries:
                    fts_query = _fts_query(rewritten)
                    if not fts_query:
                        continue
                    try:
                        fts_rows = conn.execute(
                            """
                            select chunks.*
                            from knowledge_chunks_fts fts
                            join knowledge_chunks chunks
                              on chunks.tenant_id = fts.tenant_id
                             and chunks.chunk_id = fts.chunk_id
                            where fts.tenant_id = ?
                              and knowledge_chunks_fts match ?
                            limit ?
                            """,
                            (tenant_id, fts_query, self.max_scan_chunks),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        fts_rows = []
                    for row in fts_rows:
                        if row["chunk_id"] in seen:
                            continue
                        seen.add(row["chunk_id"])
                        rows.append(row)
            if not rows:
                rows = conn.execute(
                    """
                    select *
                    from knowledge_chunks
                    where tenant_id = ?
                    order by updated_at desc, ordinal asc
                    limit ?
                    """,
                    (tenant_id, self.max_scan_chunks),
                ).fetchall()
        return rows

    def _rewrite_queries(self, query: str) -> list[str]:
        rewritten = self._rewrite.rewrite_query(query)
        domain_expansions = []
        lowered = query.lower()
        if any(term in lowered for term in ["refund", "return", "damaged", "broken"]):
            domain_expansions.append(f"{query} return refund damaged quality policy")
        if any(term in lowered for term in ["invoice", "billing", "tax"]):
            domain_expansions.append(f"{query} invoice billing tax receipt")
        if any(term in lowered for term in ["shipping", "delivery", "tracking", "delay"]):
            domain_expansions.append(f"{query} shipping delivery tracking delay")
        return list(dict.fromkeys([*rewritten, *domain_expansions]))

    def _fts_available(self, conn: sqlite3.Connection) -> bool:
        if not self.fts_enabled:
            return False
        row = conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'knowledge_chunks_fts'"
        ).fetchone()
        return row is not None

    def _score(self, query: str, content: str, title: str) -> float:
        q_tokens = tokenize(query)
        text = f"{title} {content}"
        t_tokens = tokenize(text)
        if not q_tokens or not t_tokens:
            return 0.0
        t_freq: dict[str, int] = {}
        for token in t_tokens:
            t_freq[token] = t_freq.get(token, 0) + 1
        unique_query = set(q_tokens)
        overlap = sum(1 for token in unique_query if token in t_freq)
        if overlap == 0:
            return 0.0
        title_tokens = set(tokenize(title))
        title_bonus = sum(1 for token in unique_query if token in title_tokens) * 0.3
        phrase_bonus = 1.5 if query.lower() in text.lower() else 0.0
        coverage = overlap / max(len(unique_query), 1)
        density = overlap / max(len(set(t_tokens)), 1)
        return coverage + density + title_bonus + phrase_bonus


def load_documents_from_paths(
    paths: Iterable[str | Path],
    *,
    source_label: str,
    glob_pattern: str = "**/*.md",
) -> list[KnowledgeDocumentInput]:
    documents: list[KnowledgeDocumentInput] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            file_paths = sorted(item for item in path.glob(glob_pattern) if item.is_file())
            root = path
        else:
            file_paths = [path]
            root = path.parent
        for file_path in file_paths:
            content = file_path.read_text(encoding="utf-8")
            relative = _safe_relative(file_path, root)
            title = _document_title(content) or file_path.stem.replace("-", " ").replace("_", " ").strip()
            document_id = _document_id(source_label, relative)
            documents.append(
                KnowledgeDocumentInput(
                    document_id=document_id,
                    title=title,
                    content=content,
                    source_uri=f"kb://{_slug(source_label)}/{relative.as_posix()}",
                    metadata={
                        "source_label": source_label,
                        "source_file": relative.name,
                        "source_path_hash": _hash_text(str(file_path.resolve())),
                    },
                )
            )
    return documents


def sanitize_ingest_report(report: KnowledgeIngestReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "tenant_id": report.tenant_id,
        "source_label": report.source_label,
        "document_count": report.document_count,
        "chunk_count": report.chunk_count,
        "replaced_document_count": report.replaced_document_count,
        "skipped_document_count": report.skipped_document_count,
        "content_hashes": report.content_hashes,
        "warnings": report.warnings,
    }


def sanitize_summary(summary: KnowledgeIndexSummary) -> dict[str, Any]:
    return {
        "schema_version": summary.schema_version,
        "provider": summary.provider,
        "tenant_id": summary.tenant_id,
        "document_count": summary.document_count,
        "chunk_count": summary.chunk_count,
        "source_count": summary.source_count,
        "last_ingested_at": summary.last_ingested_at.isoformat() if summary.last_ingested_at else None,
        "last_updated_at": summary.last_updated_at.isoformat() if summary.last_updated_at else None,
        "fts_enabled": summary.fts_enabled,
        "database_file": summary.database_file,
        "database_path_hash": summary.database_path_hash,
    }


def _normalize_document(doc: KnowledgeDocumentInput) -> KnowledgeDocumentInput:
    return KnowledgeDocumentInput(
        document_id=_bounded(_slug(doc.document_id), 160),
        title=_bounded(" ".join(doc.title.split()), 240) or "Untitled document",
        content=doc.content,
        source_uri=_bounded(doc.source_uri.strip(), 500),
        metadata=dict(doc.metadata or {}),
    )


def _chunk_document(
    doc: KnowledgeDocumentInput,
    *,
    chunk_chars: int,
    chunk_overlap_chars: int,
) -> list[dict[str, Any]]:
    sections = _markdown_sections(doc.content)
    chunks: list[dict[str, Any]] = []
    ordinal = 0
    for heading, section_text in sections:
        compact = section_text.strip()
        if not compact:
            continue
        cursor = 0
        while cursor < len(compact):
            part = compact[cursor : cursor + chunk_chars].strip()
            if part:
                title = doc.title if not heading else f"{doc.title} - {heading}"
                content_hash = _hash_text(part)
                chunk_id = f"{doc.document_id}:{ordinal}"
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "ordinal": ordinal,
                        "title": title,
                        "content": part,
                        "content_hash": content_hash,
                        "token_count": len(tokenize(part)),
                        "metadata": {
                            **doc.metadata,
                            "heading": heading,
                            "ordinal": ordinal,
                            "content_hash": content_hash,
                        },
                    }
                )
                ordinal += 1
            if cursor + chunk_chars >= len(compact):
                break
            cursor += chunk_chars - chunk_overlap_chars
    if chunks:
        return chunks
    content = doc.content.strip()
    if not content:
        return []
    content_hash = _hash_text(content)
    return [
        {
            "chunk_id": f"{doc.document_id}:0",
            "ordinal": 0,
            "title": doc.title,
            "content": content,
            "content_hash": content_hash,
            "token_count": len(tokenize(content)),
            "metadata": {**doc.metadata, "heading": None, "ordinal": 0, "content_hash": content_hash},
        }
    ]


def _markdown_sections(content: str) -> list[tuple[str | None, str]]:
    matches = list(HEADING_RE.finditer(content))
    if not matches:
        return [(None, content)]
    sections: list[tuple[str | None, str]] = []
    prefix = content[: matches[0].start()].strip()
    if prefix:
        sections.append((None, prefix))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        heading = match.group(2).strip()
        body = content[start:end].strip()
        sections.append((heading, f"{heading}\n{body}".strip()))
    return sections


def _document_title(content: str) -> str | None:
    match = HEADING_RE.search(content)
    if not match:
        return None
    return match.group(2).strip()


def _document_id(source_label: str, relative: Path) -> str:
    stem = _slug(relative.with_suffix("").as_posix())
    digest = _hash_text(f"{source_label}:{relative.as_posix()}")[:10]
    return _bounded(f"{stem}_{digest}", 160)


def _safe_relative(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def _fts_query(query: str) -> str:
    tokens = SAFE_FTS_TOKEN_RE.findall(query)
    return " OR ".join(dict.fromkeys(tokens[:12]))


def _hash_text(value: str) -> str:
    return hashlib.sha256(f"{KNOWLEDGE_SCHEMA_VERSION}:{value}".encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._/-]+", "-", value.strip().lower())
    normalized = normalized.replace("/", "_")
    normalized = re.sub(r"-+", "-", normalized).strip("-._")
    return normalized or "document"


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = _hash_text(value)[:10]
    return f"{value[: limit - 11]}_{digest}"


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
