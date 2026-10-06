#!/bin/bash
# Type-check against numpy 2.5's stubs, as CI does on Python 3.13, without leaving this venv
# changed: numpy 2.5.3 is unpacked into a temp dir and swapped into .venv only for the run.
set -euo pipefail
cd "$(dirname "$0")/.."
SP=$(uv run --no-sync python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
TMP=$(mktemp -d)
trap 'rm -rf "${SP}/numpy"; mv "${TMP}/numpy-orig" "${SP}/numpy"; rm -rf "${TMP}"' EXIT
uv run --no-sync python -m pip download -q --no-deps --only-binary=:all: \
	--python-version 3.13 --platform manylinux_2_28_aarch64 --platform manylinux_2_28_x86_64 \
	numpy==2.5.3 -d "${TMP}" 2>/dev/null \
	|| pip download -q --no-deps --only-binary=:all: --python-version 3.13 \
		--platform manylinux_2_28_x86_64 numpy==2.5.3 -d "${TMP}"
python3 -m zipfile -e "${TMP}"/numpy-2.5.3-*.whl "${TMP}/whl"
mv "${SP}/numpy" "${TMP}/numpy-orig"
cp -r "${TMP}/whl/numpy" "${SP}/numpy"
uv run --no-sync pyright
