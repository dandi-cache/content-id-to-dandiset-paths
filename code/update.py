import argparse
import collections
import concurrent.futures
import datetime
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


def _iter_asset_manifest_keys(s3_client: "botocore.client.BaseClient"):
    """Yield every `assets.yaml` key under `dandisets/`, in lexicographic (S3 listing) order."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_BUCKET, Prefix=_ASSETS_PREFIX):
        for entry in page.get("Contents", []):
            if entry["Key"].endswith(_ASSETS_SUFFIX):
                yield entry["Key"]


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


def _collect_records(
    s3_client: "botocore.client.BaseClient", max_workers: int, limit: int | None
) -> list[tuple[str, str, str]]:
    if limit is not None:
        # Limited (testing) run: stream manifests one at a time and stop as soon as `limit`
        # asset entries have been collected, so a small `--limit` yields a correspondingly
        # small, fast output and does not enumerate the entire `dandisets/` prefix.
        records: list[tuple[str, str, str]] = []
        for key in _iter_asset_manifest_keys(s3_client):
            records.extend(_get_info(s3_client, key))
            if len(records) >= limit:
                break
        return records[:limit]

    # Full run: fetch every manifest concurrently and aggregate all asset entries.
    keys = sorted(_iter_asset_manifest_keys(s3_client))
    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for manifest_records in executor.map(lambda key: _get_info(s3_client, key), keys):
            records.extend(manifest_records)
    return records


def _load_previous_snapshot(snapshot_file_path: pathlib.Path) -> dict[str, dict[str, list[str]]] | None:
    """Read the previous run's snapshot back into memory, or return None on a bootstrap run."""
    if not snapshot_file_path.exists():
        return None

    previous_snapshot: dict[str, dict[str, list[str]]] = {}
    with snapshot_file_path.open() as file_stream:
        for line in file_stream:
            if stripped_line := line.strip():
                previous_snapshot.update(json.loads(stripped_line))
    return previous_snapshot


def _append_changes(
    changes_file_path: pathlib.Path,
    previous_snapshot: dict[str, dict[str, list[str]]],
    current_snapshot: dict[str, dict[str, list[str]]],
) -> int:
    """Append one change record per content ID whose mapping differs from the previous run.

    Each record is one JSON value per line:
    `{"timestamp": ..., "content_id": ..., "change": "added"|"removed"|"changed", "previous": ..., "current": ...}`
    where `previous`/`current` hold the full `{"<dandiset_id>": ["<path>", ...]}` mapping
    (null on the side where the content ID does not exist). All records from one run share
    the same timestamp. Returns the number of records appended.
    """
    timestamp = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec="seconds")

    change_records = []
    for content_id in sorted(previous_snapshot.keys() | current_snapshot.keys()):
        previous = previous_snapshot.get(content_id)
        current = current_snapshot.get(content_id)
        if previous == current:
            continue

        change = "added" if previous is None else "removed" if current is None else "changed"
        change_records.append(
            {
                "timestamp": timestamp,
                "content_id": content_id,
                "change": change,
                "previous": previous,
                "current": current,
            }
        )

    if len(change_records) > 0:
        with changes_file_path.open(mode="a") as file_stream:
            for change_record in change_records:
                file_stream.write(f"{json.dumps(change_record)}\n")
    return len(change_records)


def _run(base_directory: pathlib.Path, max_workers: int, limit: int | None) -> None:
    s3_client = _build_s3_client()

    records = _collect_records(s3_client, max_workers=max_workers, limit=limit)
    if len(records) == 0:
        message = (
            f"\nNo asset entries found under `s3://{_BUCKET}/{_ASSETS_PREFIX}`.\n"
            "The DANDI archive bucket may be unreachable or its layout may have changed.\n"
        )
        raise RuntimeError(message)

    content_id_to_dandiset_paths: dict[str, dict[str, set[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    for content_id, dandiset_id, path_in_dandiset in records:
        content_id_to_dandiset_paths[content_id][dandiset_id].add(path_in_dandiset)

    # Normalize into the exact structure written to (and read back from) the snapshot file, so
    # diffing against the previous run is a plain per-content-ID equality check.
    current_snapshot = {
        content_id: {
            dandiset_id: sorted(content_id_to_dandiset_paths[content_id][dandiset_id])
            for dandiset_id in sorted(content_id_to_dandiset_paths[content_id])
        }
        for content_id in sorted(content_id_to_dandiset_paths)
    }

    derivatives_directory = base_directory / "derivatives"
    derivatives_directory.mkdir(parents=True, exist_ok=True)
    snapshot_file_path = derivatives_directory / "content_id_to_dandiset_paths.jsonl"

    # The pipeline runs on a clone of the persistent `derivatives` branch, so the previous run's
    # snapshot is already present here. Diff against it before overwriting, appending the deltas
    # to `changes.jsonl` so the cache carries an explicit change history for as long as it lives.
    # Skipped on a bootstrap run (the initial snapshot itself is the baseline), on limited
    # (testing) runs (their truncated snapshot would only log noise), and on the first full run
    # after a limited run (diffing against the truncated snapshot would log every entry it
    # dropped as a spurious change) -- the `.truncated` marker carries that last state between
    # runs on the `derivatives` branch.
    truncated_marker_file_path = derivatives_directory / ".truncated"
    if limit is not None:
        truncated_marker_file_path.write_text(
            "The snapshot was last written by a limited (testing) run and is truncated.\n"
            "The next full run re-baselines change tracking instead of diffing against it.\n"
        )
    elif truncated_marker_file_path.exists():
        print("The previous snapshot was truncated by a limited run; re-baselining change tracking.")
        truncated_marker_file_path.unlink()
    else:
        previous_snapshot = _load_previous_snapshot(snapshot_file_path)
        if previous_snapshot is not None:
            changes_file_path = derivatives_directory / "changes.jsonl"
            number_of_changes = _append_changes(
                changes_file_path=changes_file_path,
                previous_snapshot=previous_snapshot,
                current_snapshot=current_snapshot,
            )
            if number_of_changes > 0:
                print(f"Appended {number_of_changes} change record(s) to {changes_file_path}.")
            else:
                print("No changes since the previous snapshot.")

    # One JSON value per line: `{"<content_id>": {"<dandiset_id>": ["<path>", ...]}}`.
    with snapshot_file_path.open(mode="w") as file_stream:
        for content_id, dandiset_paths in current_snapshot.items():
            file_stream.write(f"{json.dumps({content_id: dandiset_paths})}\n")


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
            "Optional cap on the number of asset entries to process (the output has at most "
            "this many records). Primarily a testing knob for fast, partial runs; omit for a "
            "complete cache."
        ),
    )
    args = parser.parse_args()

    _run(base_directory=args.base_directory, max_workers=args.max_workers, limit=args.limit)
