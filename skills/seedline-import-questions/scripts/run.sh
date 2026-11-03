#!/bin/bash
set -euo pipefail
skill_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
case "$(uname -s):$(uname -m)" in
    Darwin:arm64) target=macos-arm64 ;;
    Darwin:x86_64) target=macos-x64 ;;
    *) printf '%s\n' 'Windows 请使用 scripts/run.ps1；此入口支持 Apple 芯片与 Intel Mac。' >&2; exit 2 ;;
esac
for utility in shasum curl openssl jq; do
    command -v "$utility" >/dev/null 2>&1 || { printf '缺少工具：%s\n' "$utility" >&2; exit 2; }
done
runtime_root=$skill_root
if [[ ! -f "$runtime_root/assets/$target/seedline-local/seedline-local" ]]; then
    manifest=$skill_root/assets/releases.json
    [[ -f "$manifest" ]] || { printf '%s\n' '发布文件尚未就绪，请获取完整发布版本。' >&2; exit 2; }
    version=$(jq -er '.version' "$manifest")
    url=$(jq -er --arg target "$target" '.platforms[$target].url' "$manifest")
    digest=$(jq -er --arg target "$target" '.platforms[$target].sha256' "$manifest")
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$digest" =~ ^[0-9a-f]{64}$ ]] || exit 2
    [[ "$url" == "https://github.com/dream-lyc/seedline-upload-skill/releases/download/v$version/seedline-import-questions-$version-$target.tar.gz" ]] || exit 2
    cache=${XDG_CACHE_HOME:-$HOME/.cache}/seedline-upload-skill/$version/$target
    runtime_root=$cache/seedline-import-questions
    if [[ ! -d "$runtime_root" ]]; then
        mkdir -p "$cache"
        work=$(mktemp -d "$cache/.download.XXXXXXXX")
        trap 'rm -rf -- "$work"' EXIT
        curl -q --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --connect-timeout 20 --max-time 300 "$url" --output "$work/package.tar.gz"
        actual=$(shasum -a 256 "$work/package.tar.gz"); actual=${actual%% *}
        [[ "$actual" == "$digest" ]] || { printf '%s\n' '下载文件不完整，请重新尝试。' >&2; exit 2; }
        tar -xzf "$work/package.tar.gz" -C "$work"
        mv "$work/seedline-import-questions" "$runtime_root"
    fi
fi
[[ -f "$runtime_root/SHA256SUMS" ]] || exit 2
if ! (cd "$runtime_root" && shasum -a 256 -c SHA256SUMS) >/dev/null 2>&1; then
    printf '%s\n' '工具文件不完整，请重新安装完整分发；本次未发送请求。' >&2; exit 2
fi
export PYTHONUTF8=1
exec "$runtime_root/assets/$target/seedline-local/seedline-local" "$@"
