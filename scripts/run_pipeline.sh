#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON_BIN:-}"

if [[ -z "${python_bin}" ]]; then
  if [[ -x "${repo_root}/.venv/bin/python" ]]; then
    python_bin="${repo_root}/.venv/bin/python"
  else
    python_bin="python3"
  fi
fi

data_dir="${DATA_DIR:-${repo_root}/data}"
out_dir="${OUT_DIR:-${repo_root}/out}"
artifacts_dir="${ARTIFACTS_DIR:-${repo_root}/artifacts}"
database_url="${DATABASE_URL:-sqlite:///${repo_root}/moneygraph.db}"
config_path="${CONFIG_PATH:-${repo_root}/config/default.yaml}"

mkdir -p -- "${out_dir}" "${artifacts_dir}"

exec "${python_bin}" -m moneygraph.cli analyze \
  --data "${data_dir}" \
  --out "${out_dir}" \
  --artifacts "${artifacts_dir}" \
  --config "${config_path}" \
  --database-url "${database_url}" \
  "$@"
