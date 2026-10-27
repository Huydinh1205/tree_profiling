#!/bin/bash
# Mac version: double-click this file (or drag a .las/.laz file onto it).
cd "$(dirname "$0")" || exit 1

# ---- 1. One-time setup ----
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Python and libraries. This takes a few minutes and only happens once."
  PY=""
  for c in python3.11 python3.12 python3.13 python3.10; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.10 to 3.13 was not found. Install Python 3.11 from python.org, then run this again."
    read -r -p "Press Enter to close." _; exit 1
  fi
  "$PY" -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt
  if [ $? -ne 0 ]; then
    echo "Installing the libraries failed. Send a screenshot of this window to Ryan."
    rm -rf .venv
    read -r -p "Press Enter to close." _; exit 1
  fi
fi

# ---- 2. Which file? ----
clean() {  # strip quotes, trailing spaces and the backslashes Terminal adds when you drag a file in
  local s="$1"
  s="${s%"${s##*[![:space:]]}"}"; s="${s#"${s%%[![:space:]]*}"}"
  s="${s%\"}"; s="${s#\"}"; s="${s%\'}"; s="${s#\'}"
  s="${s//\\ / }"; s="${s//\\(/(}"; s="${s//\\)/)}"
  printf '%s' "$s"
}
LAS="$(clean "$1")"
while [ ! -f "$LAS" ]; do
  [ -n "$LAS" ] && echo "Cannot find this file: $LAS"
  echo
  echo "Drag your LiDAR file (.las or .laz) into this window, or paste its path, then press Enter."
  read -r -p "File: " LAS
  LAS="$(clean "$LAS")"
done
NAME="$(basename "${LAS%.*}")"

# ---- 3. Whole file or a test square? ----
echo
echo "Press Enter to process the whole file."
echo "For a quick test on a big file, type a size in metres (for example 200) to use only a square in the middle."
read -r -p "Size (Enter = whole file): " CROP
OUT="results/$NAME"; CROPARG=()
if [ -n "$CROP" ]; then OUT="results/${NAME}_crop${CROP}m"; CROPARG=(--crop-size "$CROP"); fi

# ---- 4. Run ----
echo
echo "Working on $NAME ... results will be in $(pwd)/$OUT"
if .venv/bin/python process2_segment.py "$LAS" --out "$OUT" --site-name "$NAME" \
     --models chm_watershed dalponte_py li2012_py "${CROPARG[@]}"; then
  open "$OUT/report.html"; open "$OUT"
  echo; echo "Done. The report has opened in your browser."
else
  echo; echo "Something went wrong. Send a screenshot of this window to Ryan."
fi
read -r -p "Press Enter to close." _
