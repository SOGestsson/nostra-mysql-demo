#!/bin/bash
set -e

IMAGE="sogestsson/nostra-mysql-demo-api:latest"
SSH_USER="siggi"
# Tailscale (utan heimilisnets):
#   raspberrypi    100.108.73.62   db-api :8001, demo frontend :8080
#   fagrihvammur   100.117.16.77   consumables frontend :8080, drilling :8081
# LAN heima: DEPLOY_HOST=192.168.1.50 bash deploy-db-api.sh
SSH_HOST="${DEPLOY_HOST:-100.108.73.62}"
SSH_PASS="${SSH_PASS:-Superman}"
JWT_SECRET="${JWT_SECRET:-nostradamus-secret-key}"
CONTAINER="db-api"
PORT="8001"

OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-http://192.168.1.137:18789}"
OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"
OPENCLAW_MODEL="${OPENCLAW_MODEL:-openclaw/default}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
ASSISTANT_PROVIDER="${ASSISTANT_PROVIDER:-}"

OPENCLAW_ENV=""
if [ -n "$OPENCLAW_GATEWAY_TOKEN" ]; then
  OPENCLAW_ENV="-e OPENCLAW_GATEWAY_URL=$OPENCLAW_GATEWAY_URL -e OPENCLAW_GATEWAY_TOKEN=$OPENCLAW_GATEWAY_TOKEN -e OPENCLAW_MODEL=$OPENCLAW_MODEL"
fi
LLM_ENV=""
if [ -n "$OPENAI_API_KEY" ]; then
  LLM_ENV="$LLM_ENV -e OPENAI_API_KEY=$OPENAI_API_KEY"
  ASSISTANT_PROVIDER="${ASSISTANT_PROVIDER:-openai}"
fi
if [ -n "$ANTHROPIC_API_KEY" ]; then
  LLM_ENV="$LLM_ENV -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
  ASSISTANT_PROVIDER="${ASSISTANT_PROVIDER:-anthropic}"
fi
if [ -n "$ASSISTANT_PROVIDER" ]; then
  LLM_ENV="$LLM_ENV -e ASSISTANT_PROVIDER=$ASSISTANT_PROVIDER"
fi

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
    -e JWT_SECRET=$JWT_SECRET \
    $OPENCLAW_ENV \
    $LLM_ENV \
    $IMAGE &&
  HEALTH_URL='http://127.0.0.1:$PORT/health' &&
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

echo "==> Done. db-api: http://$SSH_HOST:$PORT/health"
