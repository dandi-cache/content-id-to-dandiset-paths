#!/usr/bin/env bash
#
# CI orchestration for the update. Keeps generated results off the code branch and runs the
# processing inside the published container via datalad-containers.
#
#   - `main`        holds only the code (this checkout).
#   - `derivatives` is a persistent DataLad dataset on its own branch, cloned standalone
#                   into scratch. The processing is recorded there with
#                   `datalad containers-run`, so every update carries full provenance (the
#                   command, the output diff, and the container image digest) and history is
#                   retained. Because the clone carries the previous run's cache,
#                   update.py merges the fresh S3 state into it: new Dandiset IDs and
#                   paths are added when first seen and existing entries are never
#                   dropped, so the cache accumulates for as long as it lives.
#   - `dist`        is the lightweight, force-recreated publication artifact consumed by
#                   downstream users (see README.md).
#
# The published image is used purely as the runtime environment: the code and the dataset
# are bind-mounted in (the image holds no code), and only the image digest is stored in the
# dataset (a small text file), so it stays annex-free and ghcr holds the bytes.
#
# This cache is the first link in the DANDI cache chain: it has no upstream input dataset and
# no `sourcedata`. code/update.py pulls the DANDI archive `assets.yaml` manifests directly
# from public S3 at run time, so the container only needs outbound network access.
#
# code/update.py and code/compress.py are the actual code and run in any environment; this
# script is only the CI orchestration around them.
#
# Required environment variables:
#   REPO_URL    Authenticated https remote for this repository (clone/push).
#   WORKSPACE   Path to the `main` checkout that holds the code (this repository).
#   IMAGE       Container image reference to run the processing in.
# Optional:
#   LIMIT        Cap on the number of asset entries update.py processes (testing knob for
#                fast, partial runs; output has at most this many records). Empty/unset means
#                a complete run.
#   GITHUB_SHA   Recorded in the provenance message to link results to the code commit.
#   RUNNER_TEMP  Scratch directory for the working clones (default: /tmp).
set -euo pipefail

: "${REPO_URL:?REPO_URL must be set}"
: "${WORKSPACE:?WORKSPACE must be set}"
: "${IMAGE:?IMAGE must be set}"
LIMIT="${LIMIT:-}"
GITHUB_SHA="${GITHUB_SHA:-unknown}"

# Only pass --limit when set, so a normal run processes the full archive.
LIMIT_ARG=""
if [ -n "${LIMIT}" ]; then
  LIMIT_ARG="--limit ${LIMIT}"
fi

BOT_NAME="github-actions[bot]"
BOT_EMAIL="github-actions[bot]@users.noreply.github.com"

DS="${RUNNER_TEMP:-/tmp}/derivatives-dataset"
DISTDIR="${RUNNER_TEMP:-/tmp}/dist-publish"

# datalad (with the container extension) from the project environment.
datalad() { uv run --project "${WORKSPACE}/envs" datalad "$@"; }

git config --global user.name "${BOT_NAME}"
git config --global user.email "${BOT_EMAIL}"

# The `derivatives` dataset is a standalone clone (not a git worktree).
rm -rf "${DS}" "${DISTDIR}"

# Reuse the persistent `derivatives` dataset branch, or bootstrap a new one.
if git ls-remote --heads "${REPO_URL}" derivatives | grep -q refs/heads/derivatives; then
  echo "Reusing the existing 'derivatives' dataset branch."
  git clone --branch derivatives --single-branch "${REPO_URL}" "${DS}"
else
  echo "Bootstrapping a new 'derivatives' DataLad dataset."
  datalad create --no-annex "${DS}"
  datalad save -d "${DS}" -m "Initialize derivatives dataset"
fi

git -C "${DS}" config user.name "${BOT_NAME}"
git -C "${DS}" config user.email "${BOT_EMAIL}"

cd "${DS}"
mkdir -p derivatives

# Carry the study-level BIDS dataset_description.json (kept on the code branch) onto the
# derivatives dataset so the published dataset is self-describing. This must be committed
# before `containers-run`, which requires a clean dataset.
cp "${WORKSPACE}/dataset_description.json" dataset_description.json
datalad save -d "${DS}" -m "Update dataset_description.json" dataset_description.json

# Pin the published image digest and register it as a container. Only the digest is stored
# (a small text file), so the dataset stays annex-free; ghcr holds the image bytes.
docker pull "${IMAGE}"
DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "${IMAGE}")
mkdir -p .datalad/environments/pipeline
printf '%s\n' "${DIGEST}" > .datalad/environments/pipeline/image
datalad containers-add pipeline --update \
  --image .datalad/environments/pipeline/image \
  --call-fmt 'docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/tmp -w /tmp -v "$WORKSPACE/code":/code:ro "$(cat {img})" {cmd}'
datalad save -m "Pin runtime container image to ${DIGEST}" .datalad

# Run the processing inside the published image. The image provides only the environment;
# the code and the dataset are bind-mounted in (see the call format), and update.py fetches
# its inputs directly from public S3, so the container needs outbound network access.
datalad containers-run -n pipeline \
  --output derivatives \
  -m "Update content-id-to-dandiset-paths (code @ ${GITHUB_SHA}; image ${DIGEST})" \
  "python /code/update.py --base-directory /tmp ${LIMIT_ARG}"

# Publish the full results to the `derivatives` branch.
git -C "${DS}" push "${REPO_URL}" HEAD:derivatives

# Build and force-publish the consumer-facing `dist` artifact from a fresh repo.
uv run --project "${WORKSPACE}/envs" python "${WORKSPACE}/code/compress.py" --base-directory "${DS}"
mkdir -p "${DISTDIR}/derivatives"
cp "${DS}"/derivatives/*.jsonl.gz "${DISTDIR}/derivatives/"
cp "${WORKSPACE}/dataset_description.json" "${DISTDIR}/dataset_description.json"
git -C "${DISTDIR}" init -q -b dist
git -C "${DISTDIR}" config user.name "${BOT_NAME}"
git -C "${DISTDIR}" config user.email "${BOT_EMAIL}"
git -C "${DISTDIR}" add dataset_description.json derivatives
git -C "${DISTDIR}" commit -q -m "Publish content-id-to-dandiset-paths"
git -C "${DISTDIR}" push -f "${REPO_URL}" dist:dist
