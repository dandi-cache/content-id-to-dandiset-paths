import argparse
import collections
import concurrent.futures
import json
import pathlib

import boto3
import botocore
import botocore.config
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


def _build_s3_client() -> "botocore.client.BaseClient":
    # `dandiarchive` is a public bucket, so requests are sent unsigned (anonymous).
    config = botocore.config.Config(signature_version=botocore.UNSIGNED)
    return boto3.client("s3", region_name=_REGION, config=config)


def _list_asset_manifest_keys(s3_client: "botocore.client.BaseClient", limit: int | None = None) -> list[str]:
    paginator = s3_client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=_BUCKET, Prefix=_ASSETS_PREFIX):
        for entry in page.get("Contents", []):
            if entry["Key"].endswith(_ASSETS_SUFFIX):
                keys.append(entry["Key"])
                # Stop listing early when limited, so a small `--limit` test run stays fast and
                # does not enumerate the entire `dandisets/` prefix.
                if limit is not None and len(keys) >= limit:
                    return sorted(keys)
    return sorted(keys)


def _get_info(s3_client: "botocore.client.BaseClient", key: str) -> list[tuple[str, str, str]]:
    response = s3_client.get_object(Bucket=_BUCKET, Key=key)
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


def _run(base_directory: pathlib.Path, max_workers: int, limit: int | None) -> None:
    s3_client = _build_s3_client()

    asset_manifest_keys = _list_asset_manifest_keys(s3_client, limit=limit)
    if len(asset_manifest_keys) == 0:
        message = (
            f"\nNo `assets.yaml` manifests found under `s3://{_BUCKET}/{_ASSETS_PREFIX}`.\n"
            "The DANDI archive bucket may be unreachable or its layout may have changed.\n"
        )
        raise RuntimeError(message)

    content_id_to_dandiset_paths: dict[str, dict[str, set[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for records in executor.map(lambda key: _get_info(s3_client, key), asset_manifest_keys):
            for content_id, dandiset_id, path_in_dandiset in records:
                content_id_to_dandiset_paths[content_id][dandiset_id].add(path_in_dandiset)

    derivatives_directory = base_directory / "derivatives"
    derivatives_directory.mkdir(parents=True, exist_ok=True)

    # One JSON value per line: `{"<content_id>": {"<dandiset_id>": ["<path>", ...]}}`.
    output_file_path = derivatives_directory / "content_id_to_dandiset_paths.jsonl"
    with output_file_path.open(mode="w") as file_stream:
        for content_id in sorted(content_id_to_dandiset_paths):
            dandiset_paths = content_id_to_dandiset_paths[content_id]
            record = {
                content_id: {
                    dandiset_id: sorted(dandiset_paths[dandiset_id]) for dandiset_id in sorted(dandiset_paths)
                }
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
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional cap on the number of `assets.yaml` manifests to process. Primarily a "
            "testing knob for fast, partial runs; omit for a complete cache."
        ),
    )
    args = parser.parse_args()

    _run(base_directory=args.base_directory, max_workers=args.max_workers, limit=args.limit)
