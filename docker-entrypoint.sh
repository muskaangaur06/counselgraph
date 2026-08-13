#!/bin/sh
# Runs as root so it can fix ownership of the two named-volume mount points
# (chroma_data/uploads_data) before dropping to appuser. Named volumes carry
# whatever UID owned them when first created; on a host that ran this image
# before it had a non-root user, that's root, which appuser can't write to.
# Re-chowning here makes every container start self-healing regardless of the
# volume's prior owner, instead of relying on the Dockerfile's mkdir+chown
# which only ever applies to a brand-new empty volume.
set -e
chown -R appuser:appuser /app/data/chroma_db /app/data/uploads
exec gosu appuser "$@"
