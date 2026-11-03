#!/bin/bash
# Connection bootstrap ONLY. No signatures, browser cookies, redirects or retries.
set +x
set -uo pipefail
umask 077
export LC_ALL=C
fail() { printf '%s\n' '连接请求未发送：参数或受限文件无效。' >&2; exit 2; }
[[ $# -eq 6 ]] || fail
operation=$1 origin=$2 request_id=$3 body=$4 status_file=$5 retry_file=$6
[[ "$request_id" != 'none' ]] || request_id=''
case "$operation" in
    init) [[ -z "$request_id" ]] || fail; method=POST; path=/api/agent-connections ;;
    claims|ack|cancel)
        [[ "$request_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || fail
        case "$operation" in
            claims) method=POST; suffix=claims ;;
            ack) method=PUT; suffix=acknowledgement ;;
            cancel) method=PUT; suffix=cancellation ;;
        esac
        path=/api/agent-connections/$request_id/$suffix ;;
    *) fail ;;
esac
pattern='^(https|http)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:([0-9]+))?$'
[[ "$origin" =~ $pattern ]] || fail
scheme=${BASH_REMATCH[1]} host=${BASH_REMATCH[2]} port=${BASH_REMATCH[4]}
[[ "$scheme" == https || "$host" == 127.0.0.1 || "$host" == '[::1]' ]] || fail
if [[ -n "$port" ]]; then
    [[ ${#port} -le 5 ]] || fail
    port=$((10#$port))
    [[ $port -gt 0 && $port -le 65535 ]] || fail
fi
facts() {
    if [[ "$OSTYPE" == darwin* ]]; then stat -f '%Lp %u %d %i' "$1" 2>/dev/null
    else stat -c '%a %u %d %i' "$1" 2>/dev/null; fi
}
[[ -f "$body" && ! -L "$body" ]] || fail
before=$(facts "$body") || fail
[[ "$before" == "600 $UID "* ]] || fail
exec 3< "$body" || fail
if [[ "$OSTYPE" == darwin* ]]; then opened=$(stat -f '%Lp %u %d %i' <&3 2>/dev/null)
else opened=$(stat -L -c '%a %u %d %i' /dev/fd/3 2>/dev/null); fi
[[ "$opened" == "$before" && ! -L "$body" ]] || fail
for output in "$status_file" "$retry_file"; do
    [[ ! -L "$output" && ! -e "$output" && "$output" != "$body" ]] || fail
done
[[ "$status_file" != "$retry_file" ]] || fail
private_dir=$(mktemp -d "${TMPDIR:-/tmp}/acebro-bootstrap.XXXXXXXX") || fail
trap 'rm -rf -- "$private_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
unset CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR
status=$(curl -q --silent --path-as-is --globoff --noproxy '*' --proxy '' \
    --proto '=http,https' --connect-timeout 10 --max-time 40 --max-filesize 65536 \
    --request "$method" --header 'Content-Type: application/json' --header 'Accept: application/json' \
    --header 'Expect:' --data-binary @- --dump-header "$private_dir/headers" \
    --output "$private_dir/response" --write-out '%{http_code}' --url "$origin$path" <&3 2>/dev/null)
code=$?
exec 3<&-
[[ "$status" =~ ^[0-9]{3}$ ]] || status=000
(set -C; printf '%s' "$status" > "$status_file") || exit 2
retry=$(awk 'tolower($1)=="retry-after:" && $2 ~ /^[0-9]+\r?$/ {gsub(/\r/,"",$2); print $2}' "$private_dir/headers" 2>/dev/null | tail -1)
(set -C; printf '%s' "$retry" > "$retry_file") || exit 2
[[ $code -eq 0 ]] || exit "$code"
[[ -f "$private_dir/response" ]] && cat "$private_dir/response"
