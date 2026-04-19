#!/bin/sh
set -e
PORT="${PORT:-19999}"
exec uvicorn duck_game_backend.main:app --host 0.0.0.0 --port "$PORT"
