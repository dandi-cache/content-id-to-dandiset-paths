import gzip
import json
import pathlib

import yaml


def _minify(file_path: pathlib.Path, /) -> None:

    with file_path.open(mode="r") as file_stream:
        content_id_to_dandiset_paths = yaml.safe_load(file_stream)

    minified_file_path = file_path.parent / f"{file_path.stem}.min.json.gz"
    with gzip.open(filename=minified_file_path, mode="wt", encoding="utf-8") as file_stream:
        json.dump(obj=content_id_to_dandiset_paths, fp=file_stream)


if __name__ == "__main__":
    repo_head = pathlib.Path(__file__).parent.parent
    yaml_file_path = repo_head / "derivatives" / "content_id_to_dandiset_paths.yaml"

    _minify(yaml_file_path)
