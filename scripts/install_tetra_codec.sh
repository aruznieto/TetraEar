#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-/tmp/tetra_codec_build}"
OSMO_DIR="${BUILD_DIR}/osmo-tetra"
ETSI_PATCH_DIR="${OSMO_DIR}/etsi_codec-patches"
CODEC_DIR="${OSMO_DIR}/codec"
CODEC_BIN_DIR="${ROOT_DIR}/tetraear/tetra_codec/bin"

ETSI_URL="http://www.etsi.org/deliver/etsi_en/300300_300399/30039502/01.03.01_60/en_30039502v010301p0.zip"
ETSI_MD5="a8115fe68ef8f8cc466f4192572a1e3e"
ETSI_ZIP="${ETSI_PATCH_DIR}/etsi_tetra_codec.zip"

mkdir -p "${BUILD_DIR}"

if [ ! -d "${OSMO_DIR}" ]; then
  git clone https://gitea.osmocom.org/tetra/osmo-tetra "${OSMO_DIR}"
fi

if [ ! -f "${ETSI_ZIP}" ]; then
  if command -v curl >/dev/null 2>&1; then
    curl -L "${ETSI_URL}" -o "${ETSI_ZIP}"
  else
    echo "curl is required to download ETSI codec (install curl or place ${ETSI_ZIP} manually)."
    exit 1
  fi
fi

if command -v md5sum >/dev/null 2>&1; then
  MD5_ACTUAL="$(md5sum "${ETSI_ZIP}" | awk '{print $1}')"
elif command -v md5 >/dev/null 2>&1; then
  MD5_ACTUAL="$(md5 -q "${ETSI_ZIP}")"
else
  MD5_ACTUAL=""
fi

if [ -n "${MD5_ACTUAL}" ] && [ "${MD5_ACTUAL}" != "${ETSI_MD5}" ]; then
  echo "ETSI codec MD5 mismatch: expected ${ETSI_MD5}, got ${MD5_ACTUAL}"
  exit 1
fi

rm -rf "${CODEC_DIR}"
mkdir -p "${CODEC_DIR}"
unzip -L "${ETSI_ZIP}" -d "${CODEC_DIR}" >/dev/null

while read -r patch_file; do
  [ -z "${patch_file}" ] && continue
  patch --batch -p1 -d "${CODEC_DIR}" < "${ETSI_PATCH_DIR}/${patch_file}"
done < "${ETSI_PATCH_DIR}/series"

make -C "${CODEC_DIR}/c-code"

mkdir -p "${CODEC_BIN_DIR}"
cp -f "${CODEC_DIR}/c-code/cdecoder" "${CODEC_DIR}/c-code/sdecoder" "${CODEC_BIN_DIR}/"

echo "Installed codec binaries to ${CODEC_BIN_DIR}"
