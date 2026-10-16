#!/bin/sh
# Seeds the active colour on first start (empty volume), renders upstream.conf from it and
# runs nginx in the foreground.
set -eu

dir=/etc/nginx/pd
if [ ! -s "$dir/active-color" ]; then
    printf '%s\n' "${PD_DEFAULT_COLOR:-blue}" > "$dir/active-color"
    echo "entrypoint: empty state volume, seeded active colour ${PD_DEFAULT_COLOR:-blue}"
fi

/usr/local/bin/switch-color.sh --no-reload "$(cat "$dir/active-color")"
exec nginx -g 'daemon off;'
