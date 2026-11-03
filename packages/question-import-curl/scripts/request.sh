#!/bin/bash
# Signed, single-attempt curl transport. Compatible with macOS Bash 3.2.
set +x
set -uo pipefail
export LC_ALL=C
umask 077
# An inherited variable can carry Bash's export attribute into a later
# assignment. Remove it before holding profile/secret-derived material.
unset secret_key profile_values signature_base signature

fail() { printf '%s\n' "请求未发送：$1" >&2; exit 2; }
usage() {
    printf '%s\n' '用法：request.sh METHOD /api/integration/v1/PATH --profile FILE [--body FILE --content-type MIME] [--idempotency-key KEY] [--status-file FILE]' >&2
    exit 2
}

[[ $# -ge 4 ]] || usage
method=$1
request_path=$2
shift 2
profile='' body='' content_type='' idempotency_key='' status_file=''
has_body=0 has_type=0 has_key=0 has_status=0 has_profile=0
while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || usage
    case "$1" in
        --profile) [[ $has_profile -eq 0 ]] || usage; profile=$2; has_profile=1 ;;
        --body) [[ $has_body -eq 0 ]] || usage; body=$2; has_body=1 ;;
        --content-type) [[ $has_type -eq 0 ]] || usage; content_type=$2; has_type=1 ;;
        --idempotency-key) [[ $has_key -eq 0 ]] || usage; idempotency_key=$2; has_key=1 ;;
        --status-file) [[ $has_status -eq 0 ]] || usage; status_file=$2; has_status=1 ;;
        *) usage ;;
    esac
    shift 2
done
[[ $has_profile -eq 1 && -n "$profile" ]] || usage
[[ "$method" =~ ^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$ ]] || fail 'HTTP方法无效'
[[ "$request_path" =~ ^/api/integration/v1(/|\?|$) ]] || fail '只允许当前服务的集成接口相对路径'
[[ "$request_path" != *[!\!-~]* && "$request_path" != *'#'* && "$request_path" != *'\'* ]] || fail '请求路径必须是无控制字符的原始ASCII URI'
path_only=${request_path%%\?*}
lower_path=$(printf '%s' "$path_only" | tr '[:upper:]' '[:lower:]')
# Preserve the URI bytes; reject traversal instead of normalizing it.
case "/${path_only#/}/" in */../*|*/./*) fail '请求路径不能包含点段' ;; esac
case "$lower_path" in *%2e*|*%2f*|*%5c*|*%0[0-9a-f]*|*%1[0-9a-f]*|*%20*|*%7f*) fail '请求路径不能包含编码路径分隔符、点或控制字符' ;; esac
uri_without_escapes=$(printf '%s' "$request_path" | sed 's/%[[:xdigit:]][[:xdigit:]]//g')
[[ "$uri_without_escapes" != *%* ]] || fail '请求URI的百分号转义无效'
[[ $has_body -eq $has_type ]] || fail '正文文件与Content-Type必须同时指定'
if [[ $has_body -eq 1 ]]; then
    [[ -f "$body" && -r "$body" ]] || fail '正文文件必须是可读普通文件'
    [[ -n "$content_type" && "$content_type" != *[!\ -~]* ]] || fail 'Content-Type必须是可见ASCII且无控制字符'
fi
if [[ $has_key -eq 1 ]]; then
    [[ "$idempotency_key" =~ ^[\!-~]{1,128}$ ]] || fail '幂等键必须是1–128个可见ASCII字符'
fi
for dependency in curl openssl jq od tr sed stat mktemp; do
    command -v "$dependency" >/dev/null 2>&1 || fail '缺少curl、OpenSSL、jq或基础Shell工具'
done
[[ -f "$profile" && -r "$profile" && ! -L "$profile" ]] || fail '凭据文件必须是当前用户的0600普通文件且不能为符号链接'

file_facts() {
    if [[ "$OSTYPE" == darwin* ]]; then
        stat -L -f '%Lp %u %d %i' "$1" 2>/dev/null
    else
        stat -L -c '%a %u %d %i' "$1" 2>/dev/null
    fi
}
profile_facts=$(file_facts "$profile") || fail '无法检查凭据文件权限'
[[ "$profile_facts" == "600 $UID "* ]] || fail '凭据文件必须归当前用户所有且权限精确为0600'
# Open once, then verify the actual descriptor, preventing a path replacement race.
exec 3< "$profile" || fail '无法读取凭据文件'
if [[ "$OSTYPE" == darwin* ]]; then
    # BSD stat with no filename uses fstat(stdin); /dev/fd on macOS is fd-fs
    # metadata and does not report the original file's device or permissions.
    descriptor_facts=$(stat -f '%Lp %u %d %i' <&3 2>/dev/null) || fail '无法检查凭据文件描述符'
else
    descriptor_facts=$(file_facts /dev/fd/3) || fail '无法检查凭据文件描述符'
fi
[[ "$descriptor_facts" == "$profile_facts" && ! -L "$profile" ]] || fail '凭据文件在打开时发生变化'
profile_values=$(jq -ner --stream '[inputs] |
    [ .[] | select(length == 2) ] as $v |
    if ($v | length) == 3 and
       ([ .[] | select(length == 1) ] | length) == 1 and
       all($v[]; (.[0] | length) == 1 and (.[1] | type) == "string") and
       ($v | map(.[0][0]) | sort) == ["access_key", "origin", "secret_key"] and
       all($v[]; .[1] | test("^[ -~]+$"))
    then ["origin", "access_key", "secret_key"][] as $k |
         $v[] | select(.[0][0] == $k) | .[1]
    else error("invalid profile") end
' <&3 2>/dev/null) || fail '凭据配置必须是无重复键的origin、access_key、secret_key字符串对象'
exec 3<&-
origin=${profile_values%%$'\n'*}
profile_values=${profile_values#*$'\n'}
access_key=${profile_values%%$'\n'*}
secret_key=${profile_values#*$'\n'}
unset profile_values
[[ "$access_key" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || fail 'AK格式无效'
if [[ $has_status -eq 1 ]]; then
    [[ -n "$status_file" && ! -L "$status_file" && ( ! -e "$status_file" || -f "$status_file" ) ]] || fail '状态输出必须是普通文件且不能为符号链接'
    [[ ! "$status_file" -ef "$profile" ]] || fail '状态输出不能覆盖凭据文件'
    if [[ $has_body -eq 1 ]]; then
        [[ ! "$status_file" -ef "$body" ]] || fail '状态输出不能覆盖正文文件'
    fi
    exec 4> "$status_file" || fail '无法预先打开HTTP状态文件'
fi

# Canonicalize the origin only; never transform the path or query.
origin=${origin%/}
origin_pattern='^(https|http)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:([0-9]+))?$'
[[ "$origin" =~ $origin_pattern ]] || fail '服务地址必须是HTTPS纯origin；HTTP仅允许回环开发地址'
scheme=${BASH_REMATCH[1]}
host=$(printf '%s' "${BASH_REMATCH[2]}" | tr '[:upper:]' '[:lower:]')
port=${BASH_REMATCH[4]}
[[ "$scheme" == https || "$host" == 127.0.0.1 || "$host" == '[::1]' ]] || fail 'HTTP仅允许回环开发地址'
if [[ -n "$port" ]]; then
    [[ ${#port} -le 5 ]] || fail '服务端口必须在1–65535之间'
    port=$((10#$port))
    [[ $port -ge 1 && $port -le 65535 ]] || fail '服务端口必须在1–65535之间'
    if [[ "$scheme:$port" == https:443 || "$scheme:$port" == http:80 ]]; then port=''; fi
fi
origin=$scheme://$host${port:+:$port}
target_uri=$origin$request_path
created=$(date +%s) || fail '无法读取系统时间'
expires=$((created + 300))
nonce=$(openssl rand -base64 16 2>/dev/null | tr '/+' '_-' | tr -d '=\n') || fail '无法生成签名nonce'
[[ "$nonce" =~ ^[A-Za-z0-9_-]{22}$ ]] || fail '无法生成签名nonce'
components='"@method" "@target-uri"'
printf -v signature_base '"@method": %s\n"@target-uri": %s' "$method" "$target_uri"
content_digest=''
if [[ $has_body -eq 1 ]]; then
    digest=$(openssl dgst -sha256 -binary < "$body" 2>/dev/null | openssl base64 -A) || fail '无法计算完整正文摘要'
    [[ "$digest" =~ ^[A-Za-z0-9+/]{43}=$ ]] || fail '无法计算完整正文摘要'
    content_digest=sha-256=:$digest:
    components=$components' "content-digest" "content-type"'
    printf -v signature_base '%s\n"content-digest": %s\n"content-type": %s' "$signature_base" "$content_digest" "$content_type"
fi
if [[ $has_key -eq 1 ]]; then
    components=$components' "idempotency-key"'
    printf -v signature_base '%s\n"idempotency-key": %s' "$signature_base" "$idempotency_key"
fi
printf -v params '(%s);created=%s;expires=%s;nonce="%s";keyid="%s";alg="hmac-sha256"' "$components" "$created" "$expires" "$nonce" "$access_key"
printf -v signature_base '%s\n"@signature-params": %s' "$signature_base" "$params"
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || fail '无法定位签名脚本'
source "$script_dir/signing.sh"
sign_hmac_sha256 || fail 'SK必须是32字节的规范无填充base64url编码，或签名计算失败'
unset secret_key signature_base

private_dir=$(mktemp -d "${TMPDIR:-/tmp}/acebro-request.XXXXXXXX") || fail '无法创建私有临时目录'
cleanup() { rm -rf -- "$private_dir"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
header_file=$private_dir/headers
{
    printf 'Signature-Input: sig1=%s\nSignature: sig1=:%s:\n' "$params" "$signature"
    if [[ $has_body -eq 1 ]]; then
        printf 'Content-Digest: %s\nContent-Type: %s\n' "$content_digest" "$content_type"
    fi
    if [[ $has_key -eq 1 ]]; then printf 'Idempotency-Key: %s\n' "$idempotency_key"; fi
    printf 'Accept: application/json\nExpect:\n'
} > "$header_file" || fail '无法写入私有签名头'
unset signature params
request_timeout=1900
# Identity is a bounded read used by the resumable connection state machine.
[[ "$method:$request_path" != 'GET:/api/integration/v1/me' ]] || request_timeout=40
curl_args=(-q --silent --show-error --path-as-is --globoff --noproxy '*' --proxy ''
    --proto '=http,https' --connect-timeout 30 --max-time "$request_timeout"
    --request "$method" --header "@$header_file" --output "$private_dir/response"
    --write-out '%{http_code}')
if [[ $has_body -eq 1 ]]; then
    # -T streams regular files and sets their actual Content-Length. --request
    # above keeps POST/PATCH/etc; --data-binary would buffer a large media file.
    curl_args+=(--upload-file "$body")
fi
curl_args+=(--url "$target_uri")
# Match a client with trust_env=False, including ambient certificate overrides.
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
unset CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR
http_status=$(curl "${curl_args[@]}" 2>/dev/null)
curl_status=$?
[[ "$http_status" =~ ^[0-9]{3}$ ]] || http_status=000
if [[ $has_status -eq 1 ]]; then
    if ! printf '%s\n' "$http_status" >&4; then
        printf '%s\n' '无法写入HTTP状态文件，操作结果待确认；请查询原账本。' >&2
        exit 23
    fi
    exec 4>&-
fi
if [[ -f "$private_dir/response" ]]; then cat "$private_dir/response"; fi
if [[ $curl_status -ne 0 ]]; then
    printf '%s\n' 'curl传输失败，操作结果待确认；请保留原账本查询，不自动重试。' >&2
    exit "$curl_status"
fi
case "$http_status" in
    2[0-9][0-9]) exit 0 ;;
    *) printf '%s\n' "HTTP $http_status：请求失败；原始响应保留于stdout，未重试或跟随重定向。" >&2; exit 22 ;;
esac
