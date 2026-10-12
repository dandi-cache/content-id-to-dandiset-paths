# DANDI Cache: `content-id-to-dandiset-paths`

Maps content ID relationship to Dandiset paths, accumulated over time.

For each content ID (the identifier embedded in a blob's S3 download URL), this cache records every Dandiset and the path(s) within that Dandiset where an asset with that content has been seen.

The cache is accumulative: each update merges the current state of the DANDI archive into the previous state of the cache, so new Dandisets and paths are added when first seen and existing entries are retained even if they later disappear upstream — the cache tracks everywhere content has ever lived for as long as the cache exists.

Updated frequently.

Primarily for use by developers.

A second output, `derivatives/asset_manifest_checked_at.zarr`, records the UTC date each `assets.jsonld` was last read, keyed by its S3 key.
It is bookkeeping rather than data, and it encodes one fact about the archive: a published version's manifest is immutable, so it is read once and never again, while `draft` is re-read oldest-first.
That is what lets a run bound how many manifests it reads without ever re-reading what cannot have changed.



## One-time use

`dist` publishes each output as a [Zarr](https://zarr.dev/) store: the cache itself is `derivatives/content_id_to_dandiset_paths.zarr/`, with the cache's other outputs beside it as stores of the same kind (`derivatives/asset_manifest_checked_at.zarr/`).
A store is one array of [JSON Lines](https://jsonlines.org/) text cut into chunk files, each holding whole lines and gzip-compressed.
It can be read with the standard library alone, or opened remotely with any Zarr library.

### Python API (recommended)

The chunk files are gzip-compressed [JSON Lines](https://jsonlines.org/): list them, then read them in order as you would any JSONL files.

```python
import gzip, json, urllib.request

chunks = "https://api.github.com/repos/dandi-cache/content-id-to-dandiset-paths/contents/derivatives/content_id_to_dandiset_paths.zarr/stream/c?ref=dist"
files = sorted((entry["download_url"] for entry in json.load(urllib.request.urlopen(chunks))), key=lambda url: int(url.rsplit("/", 1)[1]))
content_id_to_dandiset_paths = {key: value for url in files for line in gzip.decompress(urllib.request.urlopen(url).read()).decode().splitlines() if line for key, value in json.loads(line).items()}
```

Each line is one JSON record mapping a content ID to the Dandisets and paths it has been seen at:

```json
{"<content_id>": {"<dandiset_id>": ["<path/in/dandiset>", "..."]}}
```

### Remote Zarr store

Anything that opens Zarr can open the store over HTTP, reading only the chunks it needs.
The group's `zarr.json` carries the array's metadata, so opening it takes one request:

```bash
pip install "zarr>=3" fsspec aiohttp
```

```python
import json

import zarr

url = "https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/refs/heads/dist/derivatives/content_id_to_dandiset_paths.zarr"
stream = zarr.open_group(url, mode="r")["stream"]
print(stream.shape, stream.chunks)  # Bytes in all, and bytes per chunk.

# The first chunk, fetched and decompressed on its own: whole lines, each `{"<key>": <value>}`.
first_chunk = stream[: stream.chunks[0]].tobytes().decode("utf-8")
for line in first_chunk.splitlines():
    if line.strip():
        print(json.loads(line))
        break
```

Reading `stream[:]` returns the whole text; to read it in pieces, step through it by `stream.chunks[0]`, as each chunk is fetched whole.

### Save to file

A raw URL cannot be listed, so to fetch every chunk at once clone the `dist` branch, which holds nothing else:

```bash
git clone --branch dist --depth 1 https://github.com/dandi-cache/content-id-to-dandiset-paths.git
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

Each output is a store in the clone, with every chunk gzip-compressed:

```python
import gzip, json, pathlib

chunks = sorted(pathlib.Path("content-id-to-dandiset-paths/derivatives/content_id_to_dandiset_paths.zarr/stream/c").iterdir(), key=lambda path: int(path.name))
content_id_to_dandiset_paths = {key: value for chunk in chunks for line in gzip.decompress(chunk.read_bytes()).decode().splitlines() if line for key, value in json.loads(line).items()}
```
