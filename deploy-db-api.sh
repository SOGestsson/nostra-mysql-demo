#!/bin/bash
set -e

IMAGE="sogestsson/nostra-mysql-demo-api:latest"
SSH_USER="siggi"
SSH_HOST="${DEPLOY_HOST:-192.168.1.50}"
SSH_PASS="Superman"
CONTAINER="db-api"
PORT="8001"

echo "==> Build and push db-api…"
docker buildx build --platform linux/arm64 -t "$IMAGE" --push .

echo "==> Deploy on $SSH_HOST…"
REMOTE_CMD="
  docker stop $CONTAINER 2>/dev/null || true &&
  docker rm $CONTAINER 2>/dev/null || true &&
  docker pull $IMAGE &&
  docker run -d --name $CONTAINER --restart unless-stopped \
    -p $PORT:8000 \
    --add-host=host.docker.internal:host-gateway \
    --add-host=raspberrypi.local:host-gateway \
    -e MYSQL_HOST=host.docker.internal \
    -e MYSQL_PORT=4406 \
    -e MYSQL_USER=root \
    -e MYSQL_PASSWORD=Superman \
    -e MYSQL_DATABASE=smart_stock \
    -e MASTER_DB_HOST=host.docker.internal \
    -e MASTER_DB_PORT=4406 \
    -e MASTER_DB_USER=root \
    -e MASTER_DB_PASSWORD=Superman \
    -e JWT_SECRET=nostradamus-secret-key \
    $IMAGE &&
  sleep 2 &&
  curl -sf 'http://127.0.0.1:$PORT/tables/items/rows?db=consumables&limit=1' >/dev/null &&
  echo 'db-api ok' &&
  docker ps --filter name=$CONTAINER
"

SSHPASS="$SSH_PASS" sshpass -e ssh -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" "$REMOTE_CMD"

echo "==> Done. db-api: http://$SSH_HOST:$PORT/docs"
