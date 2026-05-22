import asyncio
import hashlib
import re
import sqlite3
import struct
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, List

from bot.llm.llm import EmbeddingClient
from bot.memory_bak.hybrid import normalize_scores, merge_hybrid_results, apply_mmr
from bot.memory_bak.schema import MemoryType, MemorySource, MemorySearchResult
from bot.utils.log_utils import log
from shared.constants import get_context_dir, get_memory_dir, get_runtime_app_dir, get_session_dir


def _sanitize_app_id(app_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(app_id or "main").strip())
    return normalized or "main"


class MemoryManager:
    _instances: dict[str, "MemoryManager"] = {}
    _lock = threading.Lock()

    def __new__(cls, app_id: str = "main"):
        normalized_app_id = _sanitize_app_id(app_id)
        with cls._lock:
            if normalized_app_id not in cls._instances:
                cls._instances[normalized_app_id] = super().__new__(cls)
        return cls._instances[normalized_app_id]

    def __init__(self, app_id: str = "main"):
        if getattr(self, "_initialized", False):
            return

        self._initialized = True
        self.app_id = _sanitize_app_id(app_id)
        self.workspace_dir = get_runtime_app_dir(self.app_id)
        self.context_dir = get_context_dir(self.app_id)
        self.memory_dir = get_memory_dir(self.app_id)
        self.session_dir = get_session_dir(self.app_id)
        self.user_memory_path = self.memory_dir / "USER.md"
        self.soul_memory_path = self.memory_dir / "SOUL.md"
        self.identity_memory_path = self.memory_dir / "IDENTITY.md"
        self.long_term_memory_path = self.memory_dir / "MEMORY.md"
        self.short_term_memory_template = self.memory_dir / "memory_%s.md"
        self.memory_path_map = {
            MemoryType.LONG: self.long_term_memory_path,
            MemoryType.USER: self.user_memory_path,
            MemoryType.SOUL: self.soul_memory_path,
            MemoryType.IDENTITY: self.identity_memory_path,
        }
        self.embedder = EmbeddingClient()
        self._embedding_provider_name = self.embedder.model_name or "embedding-unavailable"

        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.memory_dir / "index.db"
        self.db: Optional[sqlite3.Connection] = None
        self._bootstrap_summary: Optional[dict[str, Any]] = None
        self._init_md_file()
        # self._init_db()

    def _init_md_file(self):
        """初始化基础记忆文件。"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for path in self.memory_path_map.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            log.debug(f"初始化记忆文件 {path} 成功")

    # def _init_db(self):
    #     """初始化数据库结构。"""
    #     if self.db is not None:
    #         return
    #
    #     init_sql_file = Path(__file__).with_name("init_db.sql")
    #     with open(init_sql_file, "r", encoding="utf-8") as f:
    #         init_sql = f.read()
    #
    #     self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
    #     self.db.row_factory = sqlite3.Row
    #     self.db.executescript(init_sql)
    #     self.db.commit()
    #     log.info(f"初始化 memory_bak database 路径 {self.db_path}")

    def _normalize_memory_type(self, memory_type: MemoryType | str) -> MemoryType:
        if isinstance(memory_type, MemoryType):
            return memory_type
        return MemoryType(str(memory_type).lower())

    def _resolve_workspace_path(self, path_value: str) -> Path:
        candidate = Path(path_value)
        if not candidate.is_absolute():
            if candidate.parts and candidate.parts[0] in {"memory_bak", "session", "context"}:
                candidate = self.workspace_dir / candidate
            elif candidate.name in {"USER.md", "SOUL.md", "IDENTITY.md", "MEMORY.md"}:
                candidate = self.memory_dir / candidate.name
            else:
                candidate = self.memory_dir / candidate
        resolved = candidate.resolve()

        try:
            resolved.relative_to(self.workspace_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace: {path_value}") from exc

        return resolved

    def _is_allowed_memory_path(self, path: Path) -> bool:
        resolved = path.resolve()
        allowed_files = {
            self.user_memory_path.resolve(),
            self.soul_memory_path.resolve(),
            self.identity_memory_path.resolve(),
            self.long_term_memory_path.resolve(),
        }
        if resolved in allowed_files:
            return True

        for allowed_dir in (self.memory_dir.resolve(), self.session_dir.resolve(), self.context_dir.resolve()):
            try:
                resolved.relative_to(allowed_dir)
                return True
            except ValueError:
                continue

        return False

    async def add_file_to_memorydb(self, source: MemorySource, file_path: Path):
        if not self.db:
            return 0

        try:
            content = file_path.read_text(encoding="utf-8")
            file_hash = self._hash_content(content)

            existing = self.db.execute(
                "SELECT hash FROM files WHERE path = ?",
                [str(file_path)],
            ).fetchone()

            if existing and existing["hash"] == file_hash:
                log.debug(f"File unchanged: {file_path}")
                return 0

            chunks = self._chunk_text(content, str(file_path))
            self.db.execute("DELETE FROM chunks WHERE path = ?", [str(file_path)])

            import time
            now = int(time.time())

            texts = [chunk["text"] for chunk in chunks]
            embeddings: list[list[float] | None] = [None] * len(texts)
            if texts:
                try:
                    batch_result = await self.embedder.batch_encode(texts)
                    raw = batch_result.embeddings if hasattr(batch_result, "embeddings") else batch_result
                    if raw and len(raw) == len(texts):
                        embeddings = list(raw)
                    else:
                        log.warning(f"batch_encode 返回数量不一致 ({len(raw or [])} vs {len(texts)})")
                except Exception as emb_exc:
                    log.debug(f"batch_encode失败 ({emb_exc}); chunks 不保存 embeddings {file_path}")

            for index, chunk in enumerate(chunks):
                chunk_id = f"{file_path}:{chunk['start_line']}-{chunk['end_line']}"
                emb = embeddings[index]
                emb_blob = self._serialize_embedding(emb) if emb else None

                self.db.execute(
                    """
                    INSERT INTO chunks
                    (id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        chunk_id,
                        str(file_path),
                        source.value,
                        chunk["start_line"],
                        chunk["end_line"],
                        self._hash_content(chunk["text"]),
                        self._embedding_provider_name or "embedding-unavailable",
                        chunk["text"],
                        emb_blob,
                        now,
                    ],
                )

            stat = file_path.stat()
            self.db.execute(
                """
                INSERT OR REPLACE INTO files
                (path, source, hash, mtime, size, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    str(file_path),
                    source.value,
                    file_hash,
                    int(stat.st_mtime),
                    stat.st_size,
                    now,
                ],
            )

            self.db.commit()
            log.info(f"Indexed {len(chunks)} chunks from {file_path}")
            return len(chunks)
        except Exception as e:
            log.error(f"Error adding file {file_path}: {e}", exc_info=True)
            return 0

    def _chunk_text(self, content: str, path: str, chunk_size: int = 500) -> list[dict]:
        """按行切分 text -> chunk。"""
        lines = content.split("\n")
        chunks = []

        for index in range(0, len(lines), chunk_size):
            chunk_lines = lines[index:index + chunk_size]
            chunk_text = "\n".join(chunk_lines)
            chunks.append(
                {
                    "text": chunk_text,
                    "start_line": index + 1,
                    "end_line": min(index + chunk_size, len(lines)),
                }
            )

        return chunks

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def _build_fts_query(self, query: str) -> str:
        tokens = [token for token in re.findall(r"[\w\u4e00-\u9fff-]+", query.lower()) if token]
        if not tokens:
            return '"memory_bak"'

        unique_tokens: list[str] = []
        for token in tokens:
            if token not in unique_tokens:
                unique_tokens.append(token)

        return " OR ".join(f'"{token}"' for token in unique_tokens[:8])

    def _format_search_results_for_prompt(
            self,
            results: list[MemorySearchResult],
            max_chars: int = 2000,
    ) -> str:
        if not results:
            return ""

        lines = ["# Retrieved memory_bak context", ""]
        current_len = len(lines[0])
        for result in results:
            citation = f"{result.path}#L{result.start_line}-L{result.end_line}"
            snippet = (result.snippet or result.text or "").strip()
            entry = f"- {citation} (score={result.score:.2f})\n  {snippet}"
            if current_len + len(entry) > max_chars:
                lines.append("- Additional retrieved memories truncated due to size limit.")
                break
            lines.append(entry)
            current_len += len(entry)

        return "\n".join(lines)

    def get_static_memory_context(self, max_chars: int = 4000) -> str:
        """ 静态的记忆文件 """
        sections: list[str] = []
        remaining = max_chars
        file_specs = [
            ("Identity", self.identity_memory_path),
            ("Soul", self.soul_memory_path),
            ("User", self.user_memory_path),
            ("Long-term", self.long_term_memory_path),
        ]

        for label, path in file_specs:
            if remaining <= 0 or not path.exists():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue

            snippet = content[:remaining]
            sections.append(f"## {label}\n{snippet}")
            remaining -= len(snippet)

            if len(snippet) < len(content):
                sections.append(f"## {label}\n(Truncated)")
                break

        return "\n\n".join(sections)

    def build_bootstrap_summary(self, sync_stats: Optional[dict[str, int]] = None) -> dict[str, Any]:
        summary = {
            "app_id": self.app_id,
            "workspace_dir": str(self.workspace_dir),
            "context_dir": str(self.context_dir),
            "memory_dir": str(self.memory_dir),
            "session_dir": str(self.session_dir),
            "db_path": str(self.db_path),
            "db_initialized": self.db is not None,
            "fixed_files": {
                "USER.md": self.user_memory_path.exists(),
                "SOUL.md": self.soul_memory_path.exists(),
                "IDENTITY.md": self.identity_memory_path.exists(),
                "MEMORY.md": self.long_term_memory_path.exists(),
            },
            "sync": sync_stats or {},
        }

        if self.db is not None:
            summary["indexed_files"] = self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            summary["indexed_chunks"] = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        else:
            summary["indexed_files"] = 0
            summary["indexed_chunks"] = 0

        return summary

    async def bootstrap(self) -> dict[str, Any]:
        if self._bootstrap_summary is not None:
            return self._bootstrap_summary
        sync_stats = await self.sync()
        self._bootstrap_summary = self.build_bootstrap_summary(sync_stats)
        return self._bootstrap_summary

    async def search_for_prompt(
            self,
            query: str,
            limit: int = 5,
            max_chars: int = 2000,
    ) -> dict[str, Any]:
        results = await self.search(query, limit=limit, use_hybrid=True)
        return {
            "count": len(results),
            "results": results,
            "text": self._format_search_results_for_prompt(results, max_chars=max_chars),
        }

    async def write_memory(
            self,
            content: str,
            memory_type: MemoryType | str,
            session_id: str = "default",
    ):
        memory_type = self._normalize_memory_type(memory_type)
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        if memory_type == MemoryType.SHORT:
            path = Path(str(self.short_term_memory_template) % today_str)
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path = self.memory_path_map[memory_type]

        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n--- [{time_str}] ---\n")
            if memory_type == MemoryType.SHORT:
                f.write(f"session_id: {session_id}\n")
            f.write(content.strip() + "\n")

        await self.add_file_to_memorydb(MemorySource.MEMORY, path)
        self._bootstrap_summary = None
        return f"✅ 记忆已保存 [{memory_type.value}]: {path.name}"

    async def sync(self) -> dict:
        """ 将memory文件，会话文件同步到向量数据库中 """
        stats = {
            "files_added": 0,
            "files_updated": 0,
            "files_removed": 0,
            "chunks_created": 0,
        }

        if not self.db:
            return stats

        disk_files: dict[str, MemorySource] = {}
        for memory_file in self.memory_path_map.values():
            if memory_file.exists():
                disk_files[str(memory_file)] = MemorySource.MEMORY

        if self.memory_dir.is_dir():
            for file_path in self.memory_dir.glob("*.md"):
                if file_path.is_file():
                    disk_files[str(file_path)] = MemorySource.MEMORY

        if self.session_dir.is_dir():
            for file_path in self.session_dir.glob("session_*.jsonl"):
                if file_path.is_file():
                    disk_files[str(file_path)] = MemorySource.SESSIONS

        db_files = {
            row[0]: row[1]
            for row in self.db.execute("SELECT path, hash FROM files").fetchall()
        }

        for path_str, source in disk_files.items():
            try:
                file_path = Path(path_str)
                content = file_path.read_text(encoding="utf-8")
                new_hash = self._hash_content(content)
                if db_files.get(path_str) == new_hash:
                    continue

                was_new = path_str not in db_files
                added = await self.add_file_to_memorydb(source, file_path)
                stats["chunks_created"] += added
                if was_new:
                    stats["files_added"] += 1
                else:
                    stats["files_updated"] += 1
            except Exception as exc:
                log.warning(f"sync: error indexing {path_str}: {exc}")

        for path_str in list(db_files.keys()):
            if path_str not in disk_files:
                try:
                    self.db.execute("DELETE FROM chunks WHERE path = ?", [path_str])
                    self.db.execute("DELETE FROM files WHERE path = ?", [path_str])
                    stats["files_removed"] += 1
                except Exception as exc:
                    log.warning(f"sync: error removing orphan {path_str}: {exc}")

        self.db.commit()
        log.info(
            f"BuiltinMemoryManager.sync complete: +{stats['files_added']} added, "
            f"~{stats['files_updated']} updated, -{stats['files_removed']} removed, "
            f"{stats['chunks_created']} chunks"
        )
        return stats

    def close(self) -> None:
        if self.db:
            self.db.close()
            self.db = None

    def _async(self):
        pass

    def _compact_memory(self):
        pass

    async def search(
            self,
            query: str,
            limit: int = 5,
            sources: Optional[list[MemorySource]] = None,
            use_vector: bool = False,
            use_hybrid: bool = True,
            vector_weight: float = 0.7,
    ) -> list[MemorySearchResult]:
        if not self.db or not query.strip():
            return []

        limit = max(1, int(limit or 5))
        if use_hybrid:
            return await self._hybrid_search(query, limit, sources, vector_weight)
        if use_vector:
            return await self._vector_search(query, limit, sources)
        return await self._fts_search(query, limit, sources)

    async def _vector_search(
            self,
            query: str,
            limit: int,
            sources: Optional[list[MemorySource]],
    ) -> list[MemorySearchResult]:
        try:
            query_embedding = await self.embedder.embed(query)
            source_filter = ""
            source_values = []
            if sources:
                source_names = [source.value for source in sources]
                placeholders = ",".join("?" * len(source_names))
                source_filter = f"AND chunks.source IN ({placeholders})"
                source_values = source_names

            sql = f"""
                SELECT
                    chunks.id,
                    chunks.path,
                    chunks.source,
                    chunks.text,
                    chunks.start_line,
                    chunks.end_line,
                    chunks.embedding
                FROM chunks
                WHERE chunks.embedding IS NOT NULL
                {source_filter}
            """

            cursor = self.db.execute(sql, source_values)
            results = []
            for row in cursor.fetchall():
                embedding_blob = row["embedding"]
                if not embedding_blob:
                    continue

                chunk_embedding = self._deserialize_embedding(embedding_blob)
                similarity = self._cosine_similarity(query_embedding, chunk_embedding)
                snippet = row["text"][:200] + ("..." if len(row["text"]) > 200 else "")
                results.append(
                    MemorySearchResult(
                        id=row["id"],
                        path=row["path"],
                        source=MemorySource(row["source"]),
                        text=row["text"],
                        snippet=snippet,
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        score=similarity,
                    )
                )

            results.sort(key=lambda result: result.score, reverse=True)
            return results[:limit]
        except Exception as e:
            log.error(f"Vector search error: {e}", exc_info=True)
            return []

    async def _hybrid_search(
            self,
            query: str,
            limit: int,
            sources: Optional[list[MemorySource]],
            vector_weight: float = 0.7,
    ) -> list[MemorySearchResult]:
        vector_results = await self._vector_search(query, limit * 2, sources)
        fts_results = await self._fts_search(query, limit * 2, sources)

        vector_sr = [
            MemorySearchResult(
                id=result.id,
                text=result.text,
                path=result.path,
                source=result.source.value,
                score=result.score,
                start_line=result.start_line,
                end_line=result.end_line,
                snippet="",
            )
            for result in vector_results
        ]
        fts_sr = [
            MemorySearchResult(
                id=result.id,
                text=result.text,
                path=result.path,
                source=result.source.value,
                score=result.score,
                start_line=result.start_line,
                end_line=result.end_line,
                snippet="",
            )
            for result in fts_results
        ]

        vector_sr = normalize_scores(vector_sr)
        fts_sr = normalize_scores(fts_sr)
        text_weight = 1.0 - vector_weight
        merged = merge_hybrid_results(vector_sr, fts_sr, vector_weight, text_weight)
        merged = apply_mmr(merged, limit=limit * 2)

        results = []
        for search_result in merged[:limit]:
            snippet = search_result.text[:200] + ("..." if len(search_result.text) > 200 else "")
            results.append(
                MemorySearchResult(
                    id=search_result.id,
                    path=search_result.path,
                    source=MemorySource(search_result.source),
                    text=search_result.text,
                    snippet=snippet,
                    start_line=search_result.start_line,
                    end_line=search_result.end_line,
                    score=search_result.score,
                )
            )

        return results

    async def _fts_search(
            self,
            query: str,
            limit: int,
            sources: Optional[list[MemorySource]],
    ) -> list[MemorySearchResult]:
        try:
            fts_query = self._build_fts_query(query)
            source_filter = ""
            source_values = []
            if sources:
                source_names = [source.value for source in sources]
                placeholders = ",".join("?" * len(source_names))
                source_filter = f"AND chunks.source IN ({placeholders})"
                source_values = source_names

            sql = f"""
                SELECT
                    chunks.id,
                    chunks.path,
                    chunks.source,
                    chunks.text,
                    chunks.start_line,
                    chunks.end_line,
                    bm25(chunks_fts) as score
                FROM chunks_fts
                JOIN chunks ON chunks.rowid = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                {source_filter}
                ORDER BY score
                LIMIT ?
            """

            cursor = self.db.execute(sql, [fts_query] + source_values + [limit])
            results = []
            for row in cursor.fetchall():
                snippet = row["text"][:200] + ("..." if len(row["text"]) > 200 else "")
                results.append(
                    MemorySearchResult(
                        id=row["id"],
                        path=row["path"],
                        source=MemorySource(row["source"]),
                        text=row["text"],
                        snippet=snippet,
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        score=abs(row["score"]),
                    )
                )

            return results
        except Exception as e:
            log.error(f"FTS search error: {e}", exc_info=True)
            return []

    def _deserialize_embedding(self, blob: bytes) -> List[float]:
        num_floats = len(blob) // 4
        return list(struct.unpack(f"{num_floats}f", blob))

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        magnitude_a = sum(x * x for x in a) ** 0.5
        magnitude_b = sum(x * x for x in b) ** 0.5
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0

        return dot_product / (magnitude_a * magnitude_b)

    async def read_file(self, params: dict[str, Any]) -> dict[str, str]:
        rel_path = params.get("path") or params.get("relPath", "")
        from_line = params.get("from")
        lines_count = params.get("lines")

        try:
            file_path = self._resolve_workspace_path(rel_path)
        except ValueError as exc:
            return {"path": rel_path, "text": "", "error": str(exc)}

        if not self._is_allowed_memory_path(file_path):
            return {"path": rel_path, "text": "", "error": "Access denied"}

        if not file_path.exists():
            return {"path": rel_path, "text": "", "error": "File not found"}

        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            if from_line is not None:
                start = max(0, from_line - 1)
                end = min(len(lines), start + lines_count) if lines_count else len(lines)
                lines = lines[start:end]
            return {"path": rel_path, "text": "\n".join(lines)}
        except Exception as exc:
            log.error("read_file %s: %s", file_path, exc)
            return {"path": rel_path, "text": "", "error": str(exc)}


if __name__ == "__main__":
    async def test():
        manager = MemoryManager()
        result = await manager.search(query="测试", use_hybrid=True)
        print(result)


    asyncio.run(test())