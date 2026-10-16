#!/bin/sh
# Usage: switch-color.sh [--no-reload] <blue|green|staging>
# Points the proxy at console-<colour>. Validates the new config with `nginx -t` before
# reloading and restores the previous config if validation fails.
set -eu

reload=1
if [ "${1:-}" = "--no-reload" ]; then
    reload=0
    shift
fi

color="${1:-}"
case "$color" in
    blue|green|staging) ;;
    *) echo "usage: switch-color.sh [--no-reload] <blue|green|staging>" >&2; exit 2 ;;
esac

dir=/etc/nginx/pd
conf="$dir/upstream.conf"

[ -f "$conf" ] && cp "$conf" "$conf.bak"
# shellcheck disable=SC2016 # $pd_backend / $pd_color are nginx variables, not shell ones.
printf 'set $pd_backend http://console-%s:8000;\nset $pd_color %s;\n' "$color" "$color" > "$conf.tmp"
mv "$conf.tmp" "$conf"

if ! nginx -t -q; then
    echo "switch-color: nginx -t failed, restoring previous upstream" >&2
    [ -f "$conf.bak" ] && mv "$conf.bak" "$conf"
    exit 1
fi

# The colour file is written only after the config is known to be valid; it is what
# bluegreen.sh reads to decide which colour is live.
printf '%s\n' "$color" > "$dir/active-color.tmp"
mv "$dir/active-color.tmp" "$dir/active-color"

if [ "$reload" -eq 1 ]; then
    # Graceful: new workers take new connections, old workers finish in-flight requests.
    nginx -s reload
fi
echo "switch-color: active colour is now $color"
