#!/usr/bin/env bash
# Convenience launcher: activates the local venv and sane env defaults.
cd "$(dirname "$0")"
export WANDB_MODE=${WANDB_MODE:-offline}
export WANDB_SILENT=${WANDB_SILENT:-true}
exec .venv/bin/python "$@"
