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

Everything shared -- the argument parsing, the logging, the output paths, testing mode, the JSON
Lines writing, the unsigned S3 client, the listing, the manifest reader and the content-ID parse
-- comes from `dandi_cache_utils`, which the runtime image carries.
"""

import collections

import dandi_cache_utils as dandi_cache

#: One connection per worker; a smaller pool makes the surplus workers redo the TLS handshake.
WORKERS = 16


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()
    client = dandi_cache.s3.anonymous_client(max_pool_connections=WORKERS)

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

    def build() -> list[dict]:
        keys = list(dandi_cache.s3.asset_manifest_keys(client))
        if dataset.testing:
            # Enough manifests to exercise the real join without walking the whole bucket.
            keys = keys[: dandi_cache.TESTING_LIMIT]

        records = []
        for manifest in dandi_cache.s3.concurrent_map(manifest_records, keys, max_workers=WORKERS):
            records.extend(manifest)
        if not records:
            message = (
                f"No asset entries were found under `s3://{dandi_cache.s3.BUCKET}/"
                f"{dandi_cache.s3.DANDISETS_PREFIX}`. The archive bucket may be unreachable or its "
                "layout may have changed."
            )
            raise RuntimeError(message)

        # Seeded with what is already published, then unioned with the fresh state, so a path that
        # has since disappeared upstream is retained rather than dropped.
        paths_of: dict[str, dict[str, set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))
        for content_id, dandiset_paths in dataset.read_output_lookup().items():
            for dandiset_id, paths in dandiset_paths.items():
                paths_of[content_id][dandiset_id].update(paths)
        for content_id, dandiset_id, path in records:
            paths_of[content_id][dandiset_id].add(path)

        return [
            {
                content_id: {
                    dandiset_id: sorted(paths_of[content_id][dandiset_id])
                    for dandiset_id in sorted(paths_of[content_id])
                }
            }
            for content_id in sorted(paths_of)
        ]

    dandi_cache.run_full_rebuild(dataset, build=build, limit=arguments.limit)


if __name__ == "__main__":
    main()
