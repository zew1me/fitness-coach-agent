#!/usr/bin/env bash
# Run the pinned zew1me/actionlint fork against the workflow files.
#
# The fork carries newer workflow-schema support (e.g. checkout v7, Ubuntu 26.04 labels) than the
# upstream release or the Homebrew formula, so everyone — CI, hooks, and local runs — uses this
# exact build (CI's actionlint.yml workflow pins the same version) instead of whatever `actionlint` is on PATH. Bump ACTIONLINT_VERSION and the digests
# together; digests come from the release asset `digest` field (`gh release view`).
set -euo pipefail

ACTIONLINT_VERSION="1.8.0"
RELEASE_BASE="https://github.com/zew1me/actionlint/releases/download/v${ACTIONLINT_VERSION}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) platform="darwin_arm64" sha256="ff70da7d6fb8508d8a597114605aaceca30baa96c2872811989c51db3c3b347c" ;;
  Darwin-x86_64) platform="darwin_amd64" sha256="ff72f03f249f2f7b5bb69b7cca08d38c43cf395734d8acd0f914b915e6fe669e" ;;
  Linux-x86_64) platform="linux_amd64" sha256="1cc41d4e9ffe3f1dd04fb38b87a8a59440767a5dcab5e9d581cf1a89eca802c6" ;;
  Linux-aarch64 | Linux-arm64) platform="linux_arm64" sha256="4c1bcf048e4264f156dec083ba008276f16fca9ad4b1fda1c42456fc2538036b" ;;
  *)
    echo "error: no pinned actionlint build for $(uname -s)-$(uname -m)" >&2
    exit 2
    ;;
esac

repo_root=$(git rev-parse --show-toplevel)
install_dir="${repo_root}/.cache/actionlint/${ACTIONLINT_VERSION}"
binary="${install_dir}/actionlint"

if [[ ! -x "$binary" ]]; then
  archive="actionlint-zew1me_${ACTIONLINT_VERSION}_${platform}.tar.gz"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT

  curl --fail --silent --show-error --location --retry 3 -o "${tmp}/${archive}" \
    "${RELEASE_BASE}/${archive}"

  actual=$(shasum -a 256 "${tmp}/${archive}" | cut -d' ' -f1)
  if [[ "$actual" != "$sha256" ]]; then
    echo "error: checksum mismatch for ${archive} (expected ${sha256}, got ${actual})" >&2
    exit 1
  fi

  mkdir -p "$install_dir"
  tar -xzf "${tmp}/${archive}" -C "$install_dir" actionlint
fi

cd "$repo_root"
if [[ $# -eq 0 ]]; then
  shopt -s nullglob
  set -- .github/workflows/*.yml .github/workflows/*.yaml
fi
exec "$binary" "$@"
