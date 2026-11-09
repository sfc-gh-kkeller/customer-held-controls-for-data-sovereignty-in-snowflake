#!/usr/bin/env bash
# ── Build and push Iceberg tunnel images to Snowflake Image Registry ──────────
# Usage:
#   ./build-and-push.sh                  # tag: latest
#   ./build-and-push.sh v1.2.0           # custom tag
#
# Prerequisites:
#   docker login <REGISTRY>  (use your Snowflake account credentials)

set -euo pipefail

# ── Configuration — edit these to match your Snowflake account ───────────────
# ── Set these for your Snowflake account ─────────────────────────────────────
# Get the registry URL from:  SHOW IMAGE REPOSITORIES IN SCHEMA <db>.<schema>;
# It looks like:  <orgname>-<account>.registry.snowflakecomputing.com/<db>/<schema>/<repo>
REGISTRY="${REGISTRY:-<orgname>-<account>.registry.snowflakecomputing.com}"
REPO="${REPO:-<database>/<schema>/websocket_images}"
TAG="${1:-latest}"

echo "========================================================"
echo "  Building Iceberg Tunnel Images"
echo "========================================================"
echo "  Registry : $REGISTRY"
echo "  Repo     : $REPO"
echo "  Tag      : $TAG"
echo "========================================================"

cd "$(dirname "$0")"

# Copy pixi lockfiles needed by Dockerfiles
cp ../pixi.toml ../pixi.lock .

# ── 1. tunnel-sidecar ─────────────────────────────────────────────────────────
echo ""
echo "🔨 Building tunnel-sidecar..."
docker build \
  --platform linux/amd64 \
  -f Dockerfile.tunnel-sidecar \
  -t "tunnel-sidecar:$TAG" \
  -t "$REGISTRY/$REPO/tunnel-sidecar:$TAG" \
  .
echo "✅ tunnel-sidecar built"

# ── 2. iceberg-agent ─────────────────────────────────────────────────────────
echo ""
echo "🔨 Building iceberg-agent..."
docker build \
  --platform linux/amd64 \
  -f Dockerfile.iceberg \
  -t "iceberg-agent:$TAG" \
  -t "$REGISTRY/$REPO/iceberg-agent:$TAG" \
  .
echo "✅ iceberg-agent built"

# ── Push ─────────────────────────────────────────────────────────────────────
echo ""
echo "📤 Pushing images to $REGISTRY..."
docker push "$REGISTRY/$REPO/tunnel-sidecar:$TAG"
docker push "$REGISTRY/$REPO/iceberg-agent:$TAG"

# Clean up copied lockfiles
rm -f pixi.toml pixi.lock

echo ""
echo "========================================================"
echo "  ✅ Done!"
echo "========================================================"
echo ""
echo "  $REGISTRY/$REPO/tunnel-sidecar:$TAG"
echo "  $REGISTRY/$REPO/iceberg-agent:$TAG"
echo ""
echo "Next: run snowflake/service.sql in Snowflake to create the service."
