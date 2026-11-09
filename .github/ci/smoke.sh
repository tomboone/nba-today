#!/usr/bin/env bash
# Smoke test for the image. image-check passes the image tag as $1.
set -euo pipefail
image=$1

# curl_cffi bundles a native curl-impersonate; prove its wheel loads on the slim base (ADR-0004).
docker run --rm "$image" python -c "import curl_cffi"

help=$(docker run --rm "$image" nba-today --help)
echo "$help"
for command in run send preview health; do
  if ! grep -qw "$command" <<<"$help"; then
    echo "::error title=smoke::nba-today --help doesn't list '$command'"
    exit 1
  fi
done

echo "smoke ok"
