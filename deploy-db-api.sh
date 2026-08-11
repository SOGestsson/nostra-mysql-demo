#!/bin/bash
set -e

IMAGE="sogestsson/nostra-mysql-demo-api:latest"
SSH_USER="siggi"
# Tailscale (utan heimilisnets):
#   raspberrypi    100.108.73.62   db-api :8001, demo frontend :8080
#   fagrihvammur   100.117.16.77   consumables frontend :8080, drilling :8081
# LAN heima: DEPLOY_HOST=192.168.1.50 bash deploy-db-api.sh
SSH_HOST="${DEPLOY_HOST:-100.108.73.62}"
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
    -e FORECAST_API_URL=${FORECAST_API_URL:-https://api.nostradamus-api.com} \
    -e FORECAST_API_TIMEOUT=${FORECAST_API_TIMEOUT:-120} \
    $IMAGE &&
  HEALTH_URL='http://127.0.0.1:$PORT/tables/items/rows?db=consumables&limit=1' &&
  ok=0 &&
  for i in \$(seq 1 30); do
    if curl -sf \"\$HEALTH_URL\" >/dev/null; then
      echo \"db-api ok (attempt \$i)\"
      ok=1
      break
    fi
    sleep 2
  done &&
  if [ \"\$ok\" -ne 1 ]; then
    echo 'db-api health check failed after 30 attempts' >&2
    docker logs --tail 50 $CONTAINER >&2 || true
    exit 1
  fi &&
  docker ps --filter name=$CONTAINER
"

SSHPASS="$SSH_PASS" sshpass -e ssh -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" "$REMOTE_CMD"

echo "==> Done. db-api: http://$SSH_HOST:$PORT/docs"
