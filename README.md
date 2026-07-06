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

## Change history

Each update also diffs the fresh snapshot against the previous one and appends the deltas to an accumulating change log, `derivatives/changes.jsonl` (also available compressed on the `dist` branch as `derivatives/changes.jsonl.gz`), so changes to the mapping are tracked explicitly for as long as the cache lives.

Each line is one JSON record describing one content ID whose mapping changed during one update:

```json
{"timestamp": "<UTC ISO 8601 time of the update>", "content_id": "<content_id>", "change": "added|removed|changed", "previous": {"<dandiset_id>": ["<path/in/dandiset>", "..."]}, "current": {"<dandiset_id>": ["<path/in/dandiset>", "..."]}}
```

`previous` is `null` for `added` records and `current` is `null` for `removed` records. All records from the same update share the same timestamp. The log begins at the first update after the cache was bootstrapped; the full commit-level history of every snapshot is additionally retained on the `derivatives` branch.

### Save to file

```bash
curl https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/refs/heads/dist/derivatives/content_id_to_dandiset_paths.jsonl.gz -o content_id_to_dandiset_paths.jsonl.gz
```



## Repeated use

If you plan on using this cache regularly, clone the `dist` branch of this repository:

```bash
git clone --branch dist https://github.com/dandi-cache/content-id-to-dandiset-paths.git
```

Or, if you prefer [DataLad](https://www.datalad.org/):

```bash
datalad clone https://github.com/dandi-cache/content-id-to-dandiset-paths.git --branch derivatives
```

Then set up a CRON on your system to pull the latest version of the cache at your desired frequency.

For example, through `crontab -e`, add:

```bash
0 0 * * * git -C /path/to/content-id-to-dandiset-paths pull
```

This will minimize data overhead by only loading the most recent changes.
