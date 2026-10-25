#!/usr/bin/env bash
# Renders index.html to png/slide-NN.png, a PDF and og.png (needs Edge or EDGE=<chromium>, and internet for the fonts).
# Slide order and count come from data.js (order + backup); the deck reads ?slide=N and ?print.
# PDF name: PDF=<name>.pdf (default: pitch.pdf).
set -euo pipefail
cd "$(dirname "$0")"
PDF="${PDF:-pitch.pdf}"
EDGE="${EDGE:-/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe}"
DIR="$(pwd -W 2>/dev/null || pwd)"
URL="file:///${DIR#/}/index.html"
# Headless Edge can hang on the default profile while a browser is open.
PROFILE="$(mktemp -d)"
trap 'rm -rf "$PROFILE"' EXIT
PROF_ARG="$( (cd "$PROFILE" && (pwd -W 2>/dev/null || pwd)) )"
# Headless Edge sometimes hangs on one page: retry with a timeout.
run() { for _ in 1 2 3; do timeout 45 "$EDGE" --headless=new --disable-gpu --hide-scrollbars --user-data-dir="$PROF_ARG" "$@" >/dev/null 2>&1 && return 0; done; return 1; }
COUNT=$(python -c "import json;s=open('data.js',encoding='utf-8').read();d=json.loads(s[s.index('{'):s.rindex('}')+1]);print(len(d['order'])+len(d.get('backup',[])))")
mkdir -p png
rm -f png/slide-*.png
for i in $(seq 1 "$COUNT"); do
  NN=$(printf '%02d' "$i")
  run --force-device-scale-factor=1 --window-size=1920,1080 --virtual-time-budget=8000 \
    --screenshot="$DIR/png/slide-$NN.png" "$URL?slide=$i"
done
run --no-pdf-header-footer --virtual-time-budget=10000 --print-to-pdf="$DIR/$PDF" "$URL?print"
python -c "from PIL import Image;im=Image.open('png/slide-01.png');im.crop((0,0,1920,1008)).resize((1200,630),Image.LANCZOS).save('og.png',optimize=True)"
echo "$COUNT slides, $PDF, og.png"
