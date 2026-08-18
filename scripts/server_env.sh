#!/usr/bin/env bash

trace_g_validate_campaign_id() {
  local campaign_id="${1:-}"
  if ! [[ "$campaign_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
    echo "ERROR: invalid campaign ID" >&2
    return 1
  fi
}

trace_g_load_server_env() {
  local env_file="${1:?environment file is required}"
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" != *=* ]]; then
      echo "ERROR: malformed environment line in $env_file" >&2
      return 1
    fi
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      OLLAMA_IMAGE|OLLAMA_GPU_DEVICE|OLLAMA_NUM_PARALLEL|OLLAMA_MAX_LOADED_MODELS|OLLAMA_MAX_QUEUE|OLLAMA_CONTEXT_LENGTH|MODEL_NAME|PROFILE_ID|AGENT_IMAGE|TRACE_G_MODEL_DIR)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
      *)
        echo "ERROR: unsupported key in $env_file: $key" >&2
        return 1
        ;;
    esac
  done < "$env_file"
}
