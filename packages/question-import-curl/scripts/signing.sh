#!/bin/bash
# Internal RFC 9421 HMAC primitive. Inputs/outputs remain shell variables.
# Never use openssl -hmac/-macopt: those put the secret in process arguments.

sign_hmac_sha256() {
    local octets canonical byte escaped ipad='' opad='' count=0
    export -n octets canonical byte escaped ipad opad secret_key signature_base signature
    [[ "$secret_key" =~ ^[A-Za-z0-9_-]{43}$ ]] || return 1
    canonical=$(printf '%s=' "$secret_key" | tr '_-' '/+' |
        openssl base64 -d -A 2>/dev/null | openssl base64 -A |
        tr '/+' '_-' | tr -d '=') || return 1
    [[ "$canonical" == "$secret_key" ]] || return 1
    octets=$(printf '%s=' "$secret_key" | tr '_-' '/+' |
        openssl base64 -d -A 2>/dev/null | od -An -v -tu1) || return 1
    for byte in $octets; do
        printf -v escaped '\\%03o' "$((byte ^ 54))"
        ipad=$ipad$escaped
        printf -v escaped '\\%03o' "$((byte ^ 92))"
        opad=$opad$escaped
        count=$((count + 1))
    done
    [[ $count -eq 32 ]] || return 1
    # A SHA-256 HMAC key block is 64 bytes; the 32-byte key is zero-padded.
    while [[ $count -lt 64 ]]; do
        ipad=$ipad'\066'
        opad=$opad'\134'
        count=$((count + 1))
    done
    signature=$(
        {
            printf '%b' "$opad"
            { printf '%b' "$ipad"; printf '%s' "$signature_base"; } |
                openssl dgst -sha256 -binary
        } | openssl dgst -sha256 -binary | openssl base64 -A
    ) || return 1
    [[ "$signature" =~ ^[A-Za-z0-9+/]{43}=$ ]]
}
