# DANDI Cache: `content-id-to-dandiset-paths`

Maps content ID relationship to Dandiset paths, accumulated over time.

For each content ID (the identifier embedded in a blob's S3 download URL), this cache records every Dandiset and the path(s) within that Dandiset where an asset with that content has been seen.

The cache is accumulative: each update merges the current state of the DANDI archive into the previous state of the cache, so new Dandisets and paths are added when first seen and existing entries are retained even if they later disappear upstream — the cache tracks everywhere content has ever lived for as long as the cache exists.

Updated frequently.

Primarily for use by developers.

A second file, `derivatives/asset_manifest_checked_at.jsonl`, records the UTC date each `assets.jsonld` was last read, keyed by its S3 key.
It is bookkeeping rather than data, and it encodes one fact about the archive: a published version's manifest is immutable, so it is read once and never again, while `draft` is re-read oldest-first.
That is what lets a run bound how many manifests it reads without ever re-reading what cannot have changed.



## One-time use

If you only plan to use this cache infrequently or from disparate locations, you can directly download the latest version of the cache as a compressed [JSON Lines](https://jsonlines.org/) file from the `dist` branch:

### Python API (recommended)

```python
import gzip
import json
import urllib.request

base = "https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/refs/heads/dist/derivatives"
content_id_to_dandiset_paths = {}
for digit in "0123456789abcdef":
    with urllib.request.urlopen(f"{base}/content_id_to_dandiset_paths_{digit}.jsonl.gz") as response:
        lines = gzip.decompress(data=response.read()).decode("utf-8").splitlines()
    for line in lines:
        content_id_to_dandiset_paths.update(json.loads(line))
```

The cache is split across sixteen files by the first hexadecimal digit of the content ID, `content_id_to_dandiset_paths_0.jsonl.gz` to `content_id_to_dandiset_paths_f.jsonl.gz`, because as one file it outgrew GitHub's 100 MiB limit.
A content ID's entry is in the file named by its first digit.

Each line is one JSON record mapping a content ID to the Dandisets and paths it has been seen at:

```json
{"<content_id>": {"<dandiset_id>": ["<path/in/dandiset>", "..."]}}
```

### Save to file

```bash
for digit in 0 1 2 3 4 5 6 7 8 9 a b c d e f; do
  curl -O "https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/refs/heads/dist/derivatives/content_id_to_dandiset_paths_${digit}.jsonl.gz"
done
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
