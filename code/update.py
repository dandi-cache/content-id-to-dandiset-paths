import collections
import pathlib

import yaml


def _get_info(file_path: pathlib.Path, content_id_to_dandiset_paths: dict[str, dict[str, set[str]]]) -> None:
    """Mutates info in-place."""
    with file_path.open(mode="r") as file_stream:
        all_asset_metadata = yaml.safe_load(stream=file_stream)

    for asset_metadata in all_asset_metadata:
        content_urls = asset_metadata["contentUrl"]
        s3_download_url = content_urls[1]
        content_id = s3_download_url.split("/")[-1] if "blobs" in s3_download_url else s3_download_url.split("/")[-2]

        dandiset_id = file_path.parent.parent.name

        path_in_dandiset = asset_metadata["path"]

        if content_id not in content_id_to_dandiset_paths:
            content_id_to_dandiset_paths[content_id] = collections.defaultdict(set)

        content_id_to_dandiset_paths[content_id][dandiset_id].add(path_in_dandiset)


def _run(base_directory: pathlib.Path, /) -> None:
    asset_file_paths = sorted([path for path in (base_directory / "sourcedata").rglob(pattern="assets.yaml")])
    if len(asset_file_paths) == 0:
        message = (
            f"\nNo asset files found in `{base_directory / 'sourcedata'}`.\n"
            "Please navigate to the top-level directory (`content-id-to-dandiset-paths`) and run: \n\n"
            '\ts5cmd --no-sign-request cp "s3://dandiarchive/dandisets/*/assets.yaml" sourcedata\n\n'
        )
        raise RuntimeError(message)

    content_id_to_dandiset_paths: dict[str, dict[str, set[str]]] = collections.defaultdict(dict)
    collections.deque(
        (
            _get_info(file_path=file_path, content_id_to_dandiset_paths=content_id_to_dandiset_paths)
            for file_path in asset_file_paths
        ),
        maxlen=0,
    )

    content_id_to_dandiset_paths_frozen: dict[str, dict[str, list[str]]] = {
        content_id: {
            dandiset_id: sorted(list(paths_in_dandiset)) for dandiset_id, paths_in_dandiset in dandiset_paths.items()
        }
        for content_id, dandiset_paths in content_id_to_dandiset_paths.items()
    }

    output_file_path = base_directory / "derivatives" / "content_id_to_dandiset_paths.yaml"
    with output_file_path.open(mode="w") as file_stream:
        yaml.safe_dump(data=content_id_to_dandiset_paths_frozen, stream=file_stream)


if __name__ == "__main__":
    repo_head = pathlib.Path(__file__).parent.parent

    _run(repo_head)
