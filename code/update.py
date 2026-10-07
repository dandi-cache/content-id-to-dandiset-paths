"""Every Dandiset path each content ID is published at.

The archive is content-addressed, so one blob can appear in several Dandisets and under several
paths within one of them. This cache inverts the archive's own asset manifests into
`{content_id: {dandiset_id: [paths]}}`, reading them straight from the public bucket.

Every version of every manifest, not only `draft`: an asset a draft has since dropped is still
part of the published version that holds it, and this cache describes where a content ID *is*
published rather than only where it is currently drafted.

The cache is accumulative rather than a rebuild, and it accumulates by union rather than by
replacement. A content ID's paths are added to as they are seen, so a path that disappears
upstream is retained. Seeding `build` from what is already published is what preserves that.

The work a run does is one manifest read each, and `limit` is how many of those it does. Which
ones is decided by a fact about the archive: a *versioned* manifest is immutable, so it is read
once and never again, while `draft` is the only one that can change. So a run reads what it has
never read, then re-reads the drafts it read longest ago, and `asset_manifest_checked_at` is what
remembers which is which. Whatever a run reads, it publishes the complete cache.

Everything shared -- the argument parsing, the logging, the batch cap, the output paths, testing
mode, the JSON Lines writing, the unsigned S3 client, the listing, the manifest reader and the
content-ID parse -- comes from `dandi_cache_utils`, which the runtime image carries.
"""

import collections
import datetime

import dandi_cache_utils as dandi_cache

#: The side output: when each manifest was last attempted, keyed by its S3 key. This is what lets
#: a bounded run read the manifests it has not seen and then the drafts it saw longest ago,
#: rather than the same prefix of the listing every time.
CHECKED_AT = "asset_manifest_checked_at.jsonl"

#: The one version of a Dandiset that can change after it is written. Every other version is
#: immutable, so a manifest read once under one of them never needs reading again.
DRAFT_MANIFEST_SUFFIX = "/draft/assets.jsonld"

#: One connection per worker; a smaller pool makes the surplus workers redo the TLS handshake.
WORKERS = 16


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()
    client = dandi_cache.s3.anonymous_client(max_pool_connections=WORKERS)

    checked_at = dataset.read_output_lookup(CHECKED_AT)

    def manifest_records(key: str, /) -> list[tuple[str, str, str]]:
        """One `(content_id, dandiset_id, path)` per asset in the manifest at `key`."""
        assets = dandi_cache.s3.dandiset_assets(client, key)
        if assets is None:
            return []
        # Key layout: `dandisets/<dandiset_id>/<version>/assets.jsonld`.
        dandiset_id = key.split("/")[1]
        return [
            (dandi_cache.s3.content_id_from_content_urls(asset["contentUrl"]), dandiset_id, asset["path"])
            for asset in assets
        ]

    def select_manifests(keys: list[str], /) -> list[str]:
        """The manifests this run reads: the unread ones first, then the stalest drafts."""
        limit = dataset.limit(arguments.limit)
        batch = dandi_cache.select_new(keys, checked_at, limit=limit)
        if limit is None or len(batch) < limit:
            # Only `draft` is re-read. A versioned manifest cannot change once written, so reading
            # it again would spend the run's budget confirming what is already known.
            batch += dandi_cache.select_stale(
                [key for key in keys if key.endswith(DRAFT_MANIFEST_SUFFIX) and key in checked_at],
                checked_at,
                limit=None if limit is None else limit - len(batch),
            )
        return batch

    def build() -> list[dict]:
        keys = list(dandi_cache.s3.asset_manifest_keys(client))
        batch = select_manifests(keys)
        dandi_cache.logger.info("Reading %d of %d asset manifests this run.", len(batch), len(keys))

        records = []
        for manifest in dandi_cache.s3.concurrent_map(manifest_records, batch, max_workers=WORKERS):
            records.extend(manifest)
        if not records:
            message = (
                f"No asset entries were found under `s3://{dandi_cache.s3.BUCKET}/"
                f"{dandi_cache.s3.DANDISETS_PREFIX}`. The archive bucket may be unreachable or its "
                "layout may have changed."
            )
            raise RuntimeError(message)

        # Seeded with what is already published, then unioned with what this run read, so a path
        # that has since disappeared upstream -- or a manifest this run did not reach -- is
        # retained rather than dropped.
        paths_of: dict[str, dict[str, set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))
        for content_id, dandiset_paths in dataset.read_split_output_lookup(dataset.config.cache_file_name).items():
            for dandiset_id, paths in dandiset_paths.items():
                paths_of[content_id][dandiset_id].update(paths)
        for content_id, dandiset_id, path in records:
            paths_of[content_id][dandiset_id].add(path)

        # Every manifest this run attempted, not only the ones that held assets: an unreadable one
        # is still done for now, and stamping it is what lets the batch move past it next run.
        today = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
        checked_at.update(dict.fromkeys(batch, today))

        return [
            {
                content_id: {
                    dandiset_id: sorted(paths_of[content_id][dandiset_id])
                    for dandiset_id in sorted(paths_of[content_id])
                }
            }
            for content_id in sorted(paths_of)
        ]

    # Split across sixteen files, since as one it would pass GitHub's 100 MiB limit for a file.
    dandi_cache.run_full_rebuild(dataset, build=build, split=True)
    dataset.write_output_lookup(checked_at, CHECKED_AT)


if __name__ == "__main__":
    main()
