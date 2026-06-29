# DANDI Cache: `content-id-to-dandiset-paths`

Maps content ID relationship to current Dandiset paths.

For each content ID (the identifier embedded in a blob's S3 download URL), this cache records every Dandiset and the path(s) within that Dandiset where an asset with that content currently lives.

Updated frequently.

Primarily for use by developers.



## One-time use

If you only plan to use this cache infrequently or from disparate locations, you can directly download the latest version of the cache as a compressed [JSON Lines](https://jsonlines.org/) file from the `dist` branch:

### Python API (recommended)

```python
import gzip
import json

import requests

url = "https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/refs/heads/dist/derivatives/content_id_to_dandiset_paths.jsonl.gz"
response = requests.get(url)
lines = gzip.decompress(data=response.content).decode("utf-8").splitlines()
content_id_to_dandiset_paths = {
    content_id: dandiset_paths
    for line in lines
    for content_id, dandiset_paths in json.loads(line).items()
}
```

Each line is one JSON record mapping a content ID to the Dandisets and paths it appears at:

```json
{"<content_id>": {"<dandiset_id>": ["<path/in/dandiset>", "..."]}}
```

Merging the lines as above gives a single `dict` keyed by content ID:

```python
content_id_to_dandiset_paths["<content_id>"]  # -> {"<dandiset_id>": ["<path/in/dandiset>", "..."]}
```

### Save to file

```bash
curl https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/refs/heads/dist/derivatives/content_id_to_dandiset_paths.jsonl.gz -o content_id_to_dandiset_paths.jsonl.gz
```



## Repeated use

If you plan on using this cache regularly, clone the `dist` branch of this repository:

```bash
git clone --branch dist https://github.com/dandi-cache/content-id-to-dandiset-paths.git
```

Then set up a CRON on your system to pull the latest version of the cache at your desired frequency.

For example, through `crontab -e`, add:

```bash
0 0 * * * git -C /path/to/content-id-to-dandiset-paths pull
```

This will minimize data overhead by only loading the most recent changes.



## How it works

This cache demonstrates how generated results are kept off the code branch and records every update with full provenance.

It uses three branches:

- **`main`** holds only the code of the update logic, the runtime container definition, and the CI workflows (including building and distributing the container images).
- **`derivatives`** is a persistent [DataLad](https://www.datalad.org/) dataset on its own branch. Each update is recorded there with `datalad containers-run`, so every revision carries full provenance of the exact command, the output diff, and the runtime container image digest.
- **`dist`** is the lightweight publication artifact consumed by downstream users and preferred for one-time downloads.

This cache is the first link in the DANDI cache chain: it has no upstream input dataset and no `sourcedata`. [`code/update.py`](code/update.py) reads the `assets.yaml` manifests for every Dandiset version directly from the public `dandiarchive` S3 bucket, extracts the content ID from each asset's S3 download URL, and aggregates the Dandiset paths per content ID.

The processing runs inside a published container image (`ghcr.io/dandi-cache/content-id-to-dandiset-paths:latest`) that holds only the pinned runtime environment.

The orchestration lives in [`code/update_pipeline.sh`](code/update_pipeline.sh); the actual cache logic lives in [`code/update.py`](code/update.py).

The repository is described as a [BIDS study dataset](https://bids-specification.readthedocs.io/en/stable/common-principles.html#study-dataset) via [`dataset_description.json`](dataset_description.json) (`DatasetType: "study"`). Future enhancements may improve the provenance tracking through this mechanism in line with BEP028.



### Local development

The container image is the authoritative runtime, but you can recreate the environment locally with [uv](https://docs.astral.sh/uv/) for debugging:

```bash
uv run --project envs python code/update.py
```
