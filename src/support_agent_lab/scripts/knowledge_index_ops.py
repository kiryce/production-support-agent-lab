from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from support_agent_lab.config import Settings
from support_agent_lab.memory.sqlite_knowledge import (
    SQLiteKnowledgeIndex,
    load_documents_from_paths,
    sanitize_ingest_report,
    sanitize_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the SQLite support knowledge index.")
    parser.add_argument("--database-url", help="SQLite knowledge database URL override.")
    parser.add_argument("--tenant-id", help="Tenant id. Defaults to APP_TENANT_ID.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Ingest Markdown/text files into the SQLite knowledge index.")
    ingest.add_argument("--source", action="append", required=True, help="File or directory to ingest. Repeatable.")
    ingest.add_argument("--source-label", default="local", help="Stable source label stored with documents.")
    ingest.add_argument("--glob", default="**/*.md", help="Glob used when a source is a directory.")
    ingest.add_argument("--replace", action="store_true", help="Replace documents with the same source label first.")
    ingest.add_argument("--chunk-chars", type=int, help="Approximate chunk size in characters.")
    ingest.add_argument("--chunk-overlap-chars", type=int, help="Chunk overlap in characters.")

    search = subparsers.add_parser("search", help="Run a local smoke-test search against the SQLite index.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=4)

    subparsers.add_parser("stats", help="Print a sanitized index summary.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    try:
        index = _load_index(args, settings)
        if args.command == "ingest":
            return _ingest(args, settings, index)
        if args.command == "search":
            return _search(args, index)
        if args.command == "stats":
            return _stats(args, index)
    except Exception as exc:
        _emit_error(str(exc), json_output=args.json)
        return 2
    _emit_error(f"unsupported command: {args.command}", json_output=args.json)
    return 2


def _load_index(args: argparse.Namespace, settings: Settings) -> SQLiteKnowledgeIndex:
    database_url = args.database_url or settings.app_knowledge_database_url
    if settings.app_require_production and not settings.is_production:
        raise RuntimeError("APP_REQUIRE_PRODUCTION=true requires APP_ENV=production")
    return SQLiteKnowledgeIndex.from_url(
        database_url,
        tenant_id=args.tenant_id or settings.app_tenant_id,
        fts_enabled=settings.app_knowledge_fts_enabled,
    )


def _ingest(args: argparse.Namespace, settings: Settings, index: SQLiteKnowledgeIndex) -> int:
    chunk_chars = args.chunk_chars or settings.app_knowledge_chunk_chars
    chunk_overlap_chars = args.chunk_overlap_chars or settings.app_knowledge_chunk_overlap_chars
    if chunk_chars < 400:
        raise RuntimeError("--chunk-chars must be >= 400")
    if chunk_overlap_chars < 0:
        raise RuntimeError("--chunk-overlap-chars must be >= 0")
    if chunk_overlap_chars >= chunk_chars:
        raise RuntimeError("--chunk-overlap-chars must be smaller than --chunk-chars")
    documents = load_documents_from_paths(
        args.source,
        source_label=args.source_label,
        glob_pattern=args.glob,
    )
    if not documents:
        raise RuntimeError("no source documents matched")
    report = index.ingest_documents(
        documents,
        source_label=args.source_label,
        replace_source=args.replace,
        chunk_chars=chunk_chars,
        chunk_overlap_chars=chunk_overlap_chars,
    )
    _emit(sanitize_ingest_report(report), json_output=args.json)
    return 0


def _search(args: argparse.Namespace, index: SQLiteKnowledgeIndex) -> int:
    if args.limit < 1:
        raise RuntimeError("--limit must be >= 1")
    trace = index.search(args.query, limit=args.limit)
    payload = {
        "query": trace.query,
        "rewritten_queries": trace.rewritten_queries,
        "selected_sources": trace.selected_sources,
        "candidates_by_stage": trace.candidates_by_stage,
        "dropped_candidates": trace.dropped_candidates,
        "selected_context": [
            {
                "document_id": hit.document_id,
                "chunk_id": hit.chunk_id,
                "title": hit.title,
                "score": hit.score,
                "source_uri": hit.source_uri,
                "content_snippet": _snippet(hit.content),
            }
            for hit in trace.selected_context
        ],
    }
    _emit(payload, json_output=args.json)
    return 0


def _stats(args: argparse.Namespace, index: SQLiteKnowledgeIndex) -> int:
    _emit(sanitize_summary(index.summary()), json_output=args.json)
    return 0


def _emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if payload.get("schema_version") == "sqlite_knowledge.v1":
        print(
            "knowledge ingest "
            f"documents={payload['document_count']} "
            f"chunks={payload['chunk_count']} "
            f"replaced={payload['replaced_document_count']} "
            f"skipped={payload['skipped_document_count']} "
            f"source={payload['source_label']}"
        )
        return
    if payload.get("schema_version") == "knowledge_index_summary.v1":
        print(
            "knowledge index "
            f"documents={payload['document_count']} "
            f"chunks={payload['chunk_count']} "
            f"sources={payload['source_count']} "
            f"file={payload['database_file']}"
        )
        return
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _emit_error(message: str, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": message}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return
    print(f"knowledge index command failed: {message}", file=sys.stderr)


def _snippet(value: str, limit: int = 360) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


if __name__ == "__main__":
    raise SystemExit(main())
