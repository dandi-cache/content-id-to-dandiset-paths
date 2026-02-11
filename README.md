# DANDI Cache: Content ID → Dandiset paths

A cache of the content ID relationship to current Dandiset paths.

Updated frequently.

Primarily for use by developers.



## One-time use

If you only plan to use this cache infrequently or from disparate locations, you can directly download the latest version of the cache as a minified and compressed JSON file:

### Python API (recommended)

```python
import requests

cache_url = "https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/main/cache.json"
response = requests.get(cache_url)
cache = response.json()
```

### Save to file

```bash
curl https://raw.githubusercontent.com/dandi-cache/content-id-to-dandiset-paths/main/cache.json -o cache.json
```



## Repeated use

If you plan on using this cache regularly, clone this repository:

```bash
git clone https://github.com/dandi-cache/content-id-to-dandiset-paths.git
```

Then set up a CRON on your system to pull the latest version of the cache at your desired frequency.

For example, through `crontab -e`, add:

```bash
0 0 * * * git -C /path/to/content-id-to-dandiset-paths pull
```

This will minimize data overhead by only loading the most recent changes.
