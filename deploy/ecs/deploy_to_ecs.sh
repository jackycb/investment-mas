#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${SCRIPT_DIR}"

if [[ ! -f .env.ecs ]]; then
  echo "Missing deploy/ecs/.env.ecs. Copy .env.ecs.example to .env.ecs and edit it first."
  exit 1
fi

echo "Building and starting Investment MAS on ECS..."
docker compose -f docker-compose.ecs.yml --env-file .env.ecs up -d --build

echo "Services started. Current status:"
docker compose -f docker-compose.ecs.yml --env-file .env.ecs ps

