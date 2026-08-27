#!/usr/bin/env bash
# stage-change-pipeline 锚定检查：PR 若改动 openspec/changes/<name>/**，
# docs/stage-pipeline-log.jsonl 必须已有该 change 的运行条目，否则阻塞。
# 用法: check-stage-pipeline-log.sh <base-ref>
set -euo pipefail

base_ref="${1:?usage: check-stage-pipeline-log.sh <base-ref>}"
log_file="docs/stage-pipeline-log.jsonl"

changes=$(git diff --name-only "$base_ref"...HEAD -- 'openspec/changes/**' |
  awk -F/ 'NF >= 3 { print $3 }' | sort -u)

if [[ -z "$changes" ]]; then
  echo "No OpenSpec change touched; nothing to check."
  exit 0
fi

missing=0
for name in $changes; do
  if [[ -f "$log_file" ]] && grep -q "\"change\": *\"$name\"" "$log_file"; then
    echo "ok: $name covered by $log_file"
  else
    echo "MISSING: change '$name' has no entry in $log_file" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  echo "Run the stage-change-pipeline for the changes above and commit its logEntry." >&2
  exit 1
fi
