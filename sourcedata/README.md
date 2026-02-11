# Source data of Content ID → Dandiset paths

The source data for this cache are the raw `asset.yaml` files which exist on `s3://dandiarchive/dandisets/` for each version of each Dandiset ID.

They contain the full asset metadata records of each asset associated with the Dandiset version.

This avoids the need to query the DANDI API for this information.
