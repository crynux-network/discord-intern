from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence, Set

from community_intern.ai.interfaces import AIClient
from community_intern.config.models import KnowledgeBaseSettings
from community_intern.kb.interfaces import IndexEntry, SourceContent
from community_intern.kb.web_fetcher import WebFetcher

logger = logging.getLogger(__name__)


class FileSystemKnowledgeBase:
    def __init__(self, config: KnowledgeBaseSettings, ai_client: AIClient):
        self.config = config
        self.ai_client = ai_client

    def _normalize_file_source_id(self, *, source_id: str, sources_dir: Path) -> str:
        """
        Normalize file source IDs to be relative to sources_dir.

        The KB index stores file source IDs as paths relative to sources_dir.
        The LLM may sometimes return a path that includes the sources_dir prefix.
        """
        raw = source_id.strip()
        normalized = raw.replace("\\", "/").lstrip("/")
        sources_dir_norm = sources_dir.as_posix().rstrip("/")
        if sources_dir_norm and normalized.startswith(sources_dir_norm + "/"):
            normalized = normalized[len(sources_dir_norm) + 1 :]
        return normalized

    async def load_index_text(self) -> str:
        """Load the startup-produced index artifact as plain text."""
        index_path = Path(self.config.index_path)
        if not index_path.exists():
            return ""
        return index_path.read_text(encoding="utf-8")

    async def load_index_entries(self) -> Sequence[IndexEntry]:
        """Load the startup-produced index artifact as structured entries."""
        text = await self.load_index_text()
        entries = []
        if not text:
            return entries

        # Split by double newlines to separate entries
        chunks = text.strip().split("\n\n")
        for chunk in chunks:
            lines = chunk.strip().split("\n")
            if not lines:
                continue
            source_id = lines[0].strip()
            description = "\n".join(lines[1:]).strip()
            entries.append(IndexEntry(source_id=source_id, description=description))
        return entries

    async def build_index(self) -> None:
        """Build the startup index artifact on disk."""
        logger.info("kb.build_index_start")
        sources_dir = Path(self.config.sources_dir)
        if not sources_dir.exists():
            logger.warning("kb.sources_dir_missing path=%s", sources_dir)
            return

        # 1. Gather sources
        file_sources: Set[Path] = set()
        url_sources: Set[str] = set()

        # Load URLs from links file
        links_file = Path(self.config.links_file_path)
        if links_file.exists():
            try:
                content = links_file.read_text(encoding="utf-8")
                for line in content.splitlines():
                    url = line.strip()
                    if url and not url.startswith("#"):
                        url_sources.add(url)
                logger.info("kb.links_file_loaded path=%s count=%d", links_file, len(url_sources))
            except Exception as e:
                logger.warning("kb.links_file_read_error path=%s error=%s", links_file, e)

        for file_path in sources_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                try:
                    # Just verify it's readable text
                    file_path.read_text(encoding="utf-8")
                    file_sources.add(file_path)
                except UnicodeDecodeError:
                    logger.warning("kb.file_decode_error path=%s", file_path)
                    continue

        logger.info("kb.sources_found files=%d urls=%d", len(file_sources), len(url_sources))

        # 2. Process sources and generate summaries
        entries: list[str] = []

        # Process files
        sorted_files = sorted(file_sources)
        total_files = len(sorted_files)
        total_items = total_files + len(url_sources)
        processed_count = 0

        for i, file_path in enumerate(sorted_files, 1):
            processed_count += 1
            rel_path = file_path.relative_to(sources_dir).as_posix()
            logger.info("kb.processing_progress current=%d total=%d type=file path=%s", processed_count, total_items, rel_path)
            try:
                text = file_path.read_text(encoding="utf-8")

                summary = await self.ai_client.summarize_for_kb_index(
                    source_id=rel_path,
                    text=text,
                )
                entries.append(f"{rel_path}\n{summary}")
            except Exception as e:
                logger.error("kb.file_processing_error path=%s error=%s", file_path, e)

        # Process URLs
        # Use WebFetcher context manager to keep browser open for batch processing
        sorted_urls = sorted(url_sources)

        async with WebFetcher(self.config) as fetcher:
            for i, url in enumerate(sorted_urls, 1):
                processed_count += 1
                logger.info("kb.processing_progress current=%d total=%d type=url url=%s", processed_count, total_items, url)
                try:
                    text = await fetcher.fetch(url)
                    if not text:
                        continue

                    summary = await self.ai_client.summarize_for_kb_index(
                        source_id=url,
                        text=text,
                    )
                    entries.append(f"{url}\n{summary}")
                except Exception as e:
                    logger.error("kb.url_processing_error url=%s error=%s", url, e)

        # 3. Write index
        index_path = Path(self.config.index_path)
        index_path.parent.mkdir(parents=True, exist_ok=True)

        # Join with blank lines
        index_content = "\n\n".join(entries)
        index_path.write_text(index_content, encoding="utf-8")
        logger.info("kb.index_written path=%s entries=%d", index_path, len(entries))

    async def load_source_content(self, *, source_id: str) -> SourceContent:
        """Load full source content for a file path or URL identifier."""
        sources_dir = Path(self.config.sources_dir)

        # Check if it's a URL
        if source_id.startswith(("http://", "https://")):
             # Reuse WebFetcher logic (it handles caching)
             # Note: For single fetch, this will start/stop browser if not cached, which is heavy but safe.
             async with WebFetcher(self.config) as fetcher:
                 text = await fetcher.fetch(source_id)
                 if not text.strip():
                     raise RuntimeError(f"Failed to load URL source content: {source_id}")
                 return SourceContent(source_id=source_id, text=text)

        try:
            raw_path = Path(source_id.strip())
            sources_dir_resolved = sources_dir.resolve()

            if raw_path.is_absolute():
                resolved = raw_path.resolve()
                try:
                    rel = resolved.relative_to(sources_dir_resolved)
                except ValueError:
                    logger.warning(
                        "kb.load_file_outside_sources_dir source_id=%s path=%s sources_dir=%s",
                        source_id,
                        resolved,
                        sources_dir_resolved,
                    )
                    raise ValueError(f"File source is outside sources_dir: {source_id}")
                file_path = sources_dir_resolved / rel
            else:
                normalized_id = self._normalize_file_source_id(source_id=source_id, sources_dir=sources_dir)
                file_path = sources_dir / Path(normalized_id)

            if not file_path.exists() or not file_path.is_file():
                raise FileNotFoundError(f"KB file source not found: {source_id}")

            text = file_path.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError(f"KB file source is empty: {source_id}")
            return SourceContent(source_id=source_id, text=text)
        except UnicodeDecodeError as e:
            logger.warning("kb.load_file_decode_error source_id=%s error=%s", source_id, e)
            raise
        except OSError as e:
            logger.warning("kb.load_file_os_error source_id=%s error=%s", source_id, e)
            raise
