#!/bin/sh
# Tailwind CSS yig'ish. tools/tailwindcss.exe (v3.4.17) .gitignore'da; sha256: tools/sha256sums.txt
# Shablon/JS o'zgargandan keyin ishga tushiring va static/css/tailwind.css ni commit qiling.
cd "$(dirname "$0")/.." && ./tools/tailwindcss.exe -c tailwind.config.js -i static/css/tailwind.input.css -o static/css/tailwind.css --minify
