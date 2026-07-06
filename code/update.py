import argparse
import collections
import concurrent.futures
import json
import pathlib

import boto3
import botocore
import botocore.config
import botocore.exceptions
import yaml

# This cache is the first link in the DANDI cache chain: it has no upstream `sourcedata` and
# instead pulls its inputs directly from the public DANDI archive S3 bucket. Each Dandiset
# version publishes an `assets.yaml` manifest under `dandisets/<dandiset_id>/<version>/`; the
# manifest lists every asset with its `path` (within the Dandiset) and its `contentUrl`s (the
# second of which is the S3 download URL that embeds the content ID).
_BUCKET = "dandiarchive"
_REGION = "us-east-2"
_ASSETS_PREFIX = "dandisets/"
_ASSETS_SUFFIX = "/assets.yaml"

# Testing mode processes only this many asset entries and writes to its own designated file
# (`derivatives/testing.jsonl`), leaving the real cache untouched.
_TESTING_LIMIT = 10
_CACHE_FILE_NAME = "content_id_to_dandiset_paths.jsonl"
_TESTING_FILE_NAME = "testing.jsonl"


def _build_s3_client() -> "botocore.client.BaseClient":
    # `dandiarchive` is a public bucket, so requests are sent unsigned (anonymous).
    config = botocore.config.Config(signature_version=botocore.UNSIGNED)
    return boto3.client("s3", region_name=_REGION, config=config)


def _iter_asset_manifest_keys(s3_client: "botocore.client.BaseClient"):
    """Yield every `assets.yaml` key under `dandisets/`, in lexicographic (S3 listing) order."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_BUCKET, Prefix=_ASSETS_PREFIX):
        for entry in page.get("Contents", []):
            if entry["Key"].endswith(_ASSETS_SUFFIX):
                yield entry["Key"]


def _get_info(s3_client: "botocore.client.BaseClient", key: str) -> list[tuple[str, str, str]]:
    try:
        response = s3_client.get_object(Bucket=_BUCKET, Key=key)
    except botocore.exceptions.ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "")
        # Embargoed Dandisets list their manifests in the public bucket but deny anonymous reads
        # (AccessDenied); a manifest can also be deleted between listing and fetching (NoSuchKey).
        # Both are expected upstream states, not pipeline failures, so skip the manifest.
        if error_code in ("AccessDenied", "NoSuchKey", "403", "404"):
            print(f"Skipping inaccessible manifest `{key}` ({error_code}).", flush=True)
            return []
        raise
    all_asset_metadata = yaml.safe_load(stream=response["Body"].read()) or []

    # Key layout: `dandisets/<dandiset_id>/<version>/assets.yaml`.
    dandiset_id = key.split("/")[1]

    records: list[tuple[str, str, str]] = []
    for asset_metadata in all_asset_metadata:
        content_urls = asset_metadata["contentUrl"]
        s3_download_url = content_urls[1]
        content_id = s3_download_url.split("/")[-1] if "blobs" in s3_download_url else s3_download_url.split("/")[-2]

        path_in_dandiset = asset_metadata["path"]

        records.append((content_id, dandiset_id, path_in_dandiset))

    return records


def _collect_records(
    s3_client: "botocore.client.BaseClient", max_workers: int, testing: bool
) -> list[tuple[str, str, str]]:
    if testing:
        # Testing run: stream manifests one at a time and stop as soon as `_TESTING_LIMIT`
        # asset entries have been collected, so the run is fast and does not enumerate the
        # entire `dandisets/` prefix.
        records: list[tuple[str, str, str]] = []
        for key in _iter_asset_manifest_keys(s3_client):
            records.extend(_get_info(s3_client, key))
            if len(records) >= _TESTING_LIMIT:
                break
        return records[:_TESTING_LIMIT]

    # Full run: fetch every manifest concurrently and aggregate all asset entries.
    keys = sorted(_iter_asset_manifest_keys(s3_client))
    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for manifest_records in executor.map(lambda key: _get_info(s3_client, key), keys):
            records.extend(manifest_records)
    return records


def _load_previous_cache(cache_file_path: pathlib.Path) -> dict[str, dict[str, list[str]]]:
    """Read the previous run's cache back into memory (empty on a bootstrap run)."""
    previous_cache: dict[str, dict[str, list[str]]] = {}
    if not cache_file_path.exists():
        return previous_cache

    with cache_file_path.open() as file_stream:
        for line in file_stream:
            if stripped_line := line.strip():
                previous_cache.update(json.loads(stripped_line))
    return previous_cache


def _run(base_directory: pathlib.Path, max_workers: int, testing: bool) -> None:
    s3_client = _build_s3_client()

    records = _collect_records(s3_client, max_workers=max_workers, testing=testing)
    if len(records) == 0:
        message = (
            f"\nNo asset entries found under `s3://{_BUCKET}/{_ASSETS_PREFIX}`.\n"
            "The DANDI archive bucket may be unreachable or its layout may have changed.\n"
        )
        raise RuntimeError(message)

    content_id_to_dandiset_paths: dict[str, dict[str, set[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )

    derivatives_directory = base_directory / "derivatives"
    derivatives_directory.mkdir(parents=True, exist_ok=True)
    # Testing runs read from and write to their own designated file, so the real cache is
    # never touched.
    output_file_path = derivatives_directory / (_TESTING_FILE_NAME if testing else _CACHE_FILE_NAME)

    # The cache is accumulative: the pipeline runs on a clone of the persistent `derivatives`
    # branch, so the previous run's cache is already present here. Seed the mapping with it before
    # folding in the fresh S3 state, so new Dandiset IDs and paths are added when first seen and
    # entries that have since disappeared upstream are retained for as long as the cache lives.
    previous_cache = _load_previous_cache(output_file_path)
    for content_id, dandiset_paths in previous_cache.items():
        for dandiset_id, paths_in_dandiset in dandiset_paths.items():
            content_id_to_dandiset_paths[content_id][dandiset_id].update(paths_in_dandiset)

    for content_id, dandiset_id, path_in_dandiset in records:
        content_id_to_dandiset_paths[content_id][dandiset_id].add(path_in_dandiset)

    # One JSON value per line: `{"<content_id>": {"<dandiset_id>": ["<path>", ...]}}`.
    with output_file_path.open(mode="w") as file_stream:
        for content_id in sorted(content_id_to_dandiset_paths):
            dandiset_paths = content_id_to_dandiset_paths[content_id]
            record = {
                content_id: {dandiset_id: sorted(dandiset_paths[dandiset_id]) for dandiset_id in sorted(dandiset_paths)}
            }
            file_stream.write(f"{json.dumps(record)}\n")


if __name__ == "__main__":
    default_base_directory = pathlib.Path(__file__).parent.parent

    parser = argparse.ArgumentParser(description="Update the content-id-to-dandiset-paths DANDI cache.")
    parser.add_argument(
        "--base-directory",
        type=pathlib.Path,
        default=default_base_directory,
        help=(
            "The directory containing the `derivatives` directory. "
            "Set to the mounted dataset path when run inside the pipeline container; "
            "defaults to the repository root."
        ),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Number of concurrent S3 download workers used to fetch the asset manifests.",
    )
    parser.add_argument(
        "--testing",
        action="store_true",
        help=(
            f"Run in testing mode: process only the first {_TESTING_LIMIT} asset entries from S3 "
            f"and read/write `derivatives/{_TESTING_FILE_NAME}` instead of the real cache, "
            "leaving it untouched. Omit for a complete update."
        ),
    )
    args = parser.parse_args()

    _run(base_directory=args.base_directory, max_workers=args.max_workers, testing=args.testing)
