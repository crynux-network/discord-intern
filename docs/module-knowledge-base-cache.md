# Module Design: Knowledge Base Cache and Incremental Updates

## Purpose

This document specifies the cache metadata and incremental update mechanism used by the Knowledge Base module to update `index.txt` without re-summarizing unchanged sources.

The mechanism applies to:

- Local file sources discovered under `kb.sources_dir`
- Web URL sources listed in `kb.links_file_path`

## Goals

- The Knowledge Base MUST avoid calling the AI summarization method when a source's content has not changed.
- The Knowledge Base MUST avoid re-fetching all URLs on every incremental update run.
- The Knowledge Base MUST produce a stable and human-readable `index.txt`.
- The Knowledge Base MUST keep `source_id` stable across runs.

## Runtime configuration

All configuration is loaded from `config.yaml` with environment-variable overrides as specified in `docs/configuration.md`.

This mechanism reads these keys under the `kb` section:

- `kb.index_cache_path`
- `kb.url_refresh_min_interval_seconds`
- `kb.url_refresh_max_age_seconds`
- `kb.url_refresh_budget_per_init`
- `kb.url_refresh_fail_backoff_base_seconds`
- `kb.url_refresh_fail_backoff_max_seconds`
- `kb.runtime_refresh_enabled`
- `kb.runtime_refresh_tick_seconds`
- `kb.url_refresh_budget_per_tick`
- `kb.file_watch_enabled`
- `kb.file_watch_debounce_seconds`
- `kb.links_watch_enabled`
- `kb.links_watch_debounce_seconds`

## Cache artifact

### File name and location

The Knowledge Base MUST persist cache metadata to the path configured by `kb.index_cache_path`.

The cache file MUST be a UTF-8 encoded JSON file.

### Atomic writes and locking

- The Knowledge Base MUST write the cache file atomically by writing to a temporary file and then renaming it to `kb.index_cache_path`.
- The Knowledge Base MUST ensure that only one writer updates `kb.index_path` and `kb.index_cache_path` at a time.

### Schema

The cache JSON MUST contain:

- `schema_version`: integer
- `generated_at`: RFC 3339 timestamp in UTC
- `sources`: object keyed by `source_id`

Each `sources[source_id]` record MUST contain:

- `source_type`: `"file"` or `"url"`
- `content_hash`: SHA-256 hex digest of the normalized source content
- `summary_text`: the description text used to generate the `index.txt` entry for this source, excluding the identifier line
- `last_indexed_at`: RFC 3339 timestamp in UTC

File records MUST additionally contain a `file` object with:

- `rel_path`: the file path relative to `kb.sources_dir`
- `size_bytes`: integer
- `mtime_ns`: integer

URL records MUST additionally contain a `url` object with:

- `url`: full URL string
- `last_fetched_at`: RFC 3339 timestamp in UTC
- `etag`: nullable string
- `last_modified`: nullable string
- `fetch_status`: `"success"`, `"not_modified"`, `"timeout"`, or `"error"`
- `consecutive_failures`: integer
- `next_check_at`: RFC 3339 timestamp in UTC

## Source identifiers

- For file sources, `source_id` MUST be the file path relative to `kb.sources_dir`.
- For URL sources, `source_id` MUST be the full URL.

The `source_id` string MUST be used as the identifier line in `index.txt`.

## Content hashing and normalization

The Knowledge Base MUST compute `content_hash` from normalized UTF-8 text.

- For file sources, the Knowledge Base MUST decode file bytes as UTF-8 text.
- For URL sources, the Knowledge Base MUST hash the extracted `<body>` text produced by the web fetching mechanism.

Before hashing, the Knowledge Base MUST normalize the text as follows:

- Convert line endings to `\n`.
- Remove trailing whitespace on each line.
- Remove leading and trailing blank lines.

## Operational entrypoints and lifecycle

The Knowledge Base MUST support two operational entrypoints that execute the same incremental update algorithm:

- `init_kb` command
- Application startup sync

The application startup sync MUST run once during application startup to ensure `kb.index_path` and `kb.index_cache_path` are present and consistent with the current file and URL source sets.

The `init_kb` command MUST remain available as an operational entrypoint to run the same incremental update algorithm on demand.

## Incremental update algorithm

### Source discovery

On `init_kb`, the Knowledge Base MUST compute the current set of sources:

- File sources: all supported text files under `kb.sources_dir`
- URL sources: all non-empty lines in `kb.links_file_path`

### Deletions

If a `source_id` exists in the cache but is not present in the current source set, the Knowledge Base MUST:

- Remove the record from the cache
- Exclude the source from `index.txt`

### New sources

If a `source_id` is present in the current source set but not present in the cache, the Knowledge Base MUST:

- Load the source content
- Compute `content_hash`
- Call the AI module summarization method to generate `summary_text`
- Store the record in the cache

### File sources: change detection

For a cached file source, the Knowledge Base MUST determine whether content hashing is required:

- If both `size_bytes` and `mtime_ns` match the cached values, the file MUST be treated as unchanged and the AI MUST NOT be called.
- Otherwise, the Knowledge Base MUST read the file content and compute `content_hash`.
  - If `content_hash` differs from the cached value, the AI module summarization method MUST be called and `summary_text` MUST be updated.
  - If `content_hash` is unchanged, the AI MUST NOT be called and only metadata MUST be updated.

### URL sources: refresh scheduling

The Knowledge Base MUST store `next_check_at` for each URL.

On `init_kb`, the Knowledge Base MUST select URLs to check as follows:

- Eligible URLs are those where `next_check_at` is less than or equal to the current time.
- Eligible URLs MUST be processed in ascending order of `next_check_at`.
- The number of processed URLs MUST NOT exceed `kb.url_refresh_budget_per_init`.

URLs not processed due to budget limits MUST remain scheduled for future runs.

### URL sources: conditional requests

When checking a URL, the Knowledge Base MUST attempt to avoid downloading unchanged content:

- If `etag` is present, the Knowledge Base MUST send an `If-None-Match` request header.
- If `last_modified` is present, the Knowledge Base MUST send an `If-Modified-Since` request header.

If the server responds with HTTP 304:

- The Knowledge Base MUST set `fetch_status` to `"not_modified"`.
- The Knowledge Base MUST update `last_fetched_at`.
- The Knowledge Base MUST NOT download the response body.
- The Knowledge Base MUST NOT call the AI.
- The Knowledge Base MUST set `next_check_at` to `now + kb.url_refresh_min_interval_seconds`.

If the server responds with HTTP 200:

- The Knowledge Base MUST fetch content using the web fetching mechanism defined in `docs/module-knowledge-base.md`.
- The Knowledge Base MUST compute `content_hash` for the extracted content.
  - If `content_hash` differs from the cached value, the AI module summarization method MUST be called and `summary_text` MUST be updated.
  - If `content_hash` is unchanged, the AI MUST NOT be called.
- The Knowledge Base MUST update `etag` and `last_modified` when present in the response.
- The Knowledge Base MUST set `fetch_status` to `"success"`.
- The Knowledge Base MUST set `next_check_at` to `now + kb.url_refresh_min_interval_seconds`.

### URL sources: failures and backoff

If a URL check fails due to timeout or error:

- The Knowledge Base MUST set `fetch_status` to `"timeout"` or `"error"`.
- The Knowledge Base MUST increment `consecutive_failures`.
- The Knowledge Base MUST set:

\[
next\_check\_delay = kb.url\_refresh\_fail\_backoff\_base\_seconds \times 2^{(consecutive\_failures - 1)}
\]

- The Knowledge Base MUST cap `next_check_delay` to `kb.url_refresh_fail_backoff_max_seconds`.
- The Knowledge Base MUST set `next_check_at` to `now + next_check_delay`.
- The Knowledge Base MUST NOT modify `content_hash` or `summary_text` on failure.

### Maximum age handling

If a URL's age since `last_fetched_at` exceeds `kb.url_refresh_max_age_seconds`, it MUST be treated as eligible for refresh scheduling, subject to `kb.url_refresh_budget_per_init`.

## Index generation

After applying all updates:

- The Knowledge Base MUST generate `index.txt` by rewriting the full file contents.
- The Knowledge Base MUST use cached `summary_text` for unchanged sources.
- The Knowledge Base MUST NOT call the AI during index generation.
- The Knowledge Base MUST order entries deterministically:
  - File sources first, then URL sources
  - Within each group, sort by `source_id` ascending

Each entry in `index.txt` MUST follow the format defined in `docs/module-knowledge-base.md`.

## Runtime refresh for long-running processes

When the application runs continuously, the Knowledge Base MUST be able to refresh the cache and `index.txt` in response to source changes.

Runtime refresh is enabled when `kb.runtime_refresh_enabled` is true.

### Consistency and concurrency

- Runtime refresh MUST use the same cache file (`kb.index_cache_path`) and index file (`kb.index_path`) as `init_kb`.
- Runtime refresh MUST use the same single-writer lock as `init_kb`.
- All writes to `kb.index_cache_path` and `kb.index_path` MUST remain atomic.

### File change detection

When `kb.file_watch_enabled` is true:

- The Knowledge Base MUST monitor `kb.sources_dir` for file create, modify, move, and delete events.
- The Knowledge Base MUST debounce events for the same file path using `kb.file_watch_debounce_seconds`.
- After debounce, the Knowledge Base MUST re-run the file source update rules in this document for the affected file path only.
- If a file is deleted, the Knowledge Base MUST remove it from the cache and from `index.txt`.

### Links list change detection

When `kb.links_watch_enabled` is true:

- The Knowledge Base MUST monitor `kb.links_file_path` for modifications.
- The Knowledge Base MUST debounce link list changes using `kb.links_watch_debounce_seconds`.
- After debounce, the Knowledge Base MUST re-read `kb.links_file_path` and compute the URL source set.

For URL sources removed from `kb.links_file_path`:

- The Knowledge Base MUST remove the corresponding `source_id` records from the cache.
- The Knowledge Base MUST remove those entries from `index.txt`.

For URL sources added to `kb.links_file_path`:

- The Knowledge Base MUST create cache records with `consecutive_failures = 0`.
- The Knowledge Base MUST set `next_check_at` to the current time to make the URL eligible for the next scheduler tick.

### URL refresh scheduler loop

Runtime refresh MUST include a scheduler loop that runs every `kb.runtime_refresh_tick_seconds` seconds.

On each tick:

- The Knowledge Base MUST select eligible URL sources where `next_check_at` is less than or equal to the current time.
- The Knowledge Base MUST process eligible URLs in ascending order of `next_check_at`.
- The number of processed URLs MUST NOT exceed `kb.url_refresh_budget_per_tick`.
- Each processed URL MUST follow the URL rules in this document, including conditional requests, hashing, AI summarization only on `content_hash` change, and failure backoff.

### Index regeneration trigger

During runtime refresh, the Knowledge Base MUST regenerate `index.txt` when any of the following occurs:

- A file source cache record is added, updated, or removed.
- A URL source cache record is added, updated, or removed.

Index regeneration MUST follow the deterministic ordering rules in this document.

## Example artifacts

- An example cache file is provided at `examples/index-cache.json`.
