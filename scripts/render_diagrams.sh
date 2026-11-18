#!/bin/sh
# Re-render every diagram in docs/diagrams from its source (light and dark, SVG and PNG).
#   docs/diagrams/src/*.dot  -> Graphviz dot
#   docs/diagrams/src/*.json -> docs/diagrams/src/sequence.py (sequence diagrams)
# Needs: dot (Graphviz), rsvg-convert (librsvg), python3. No network.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC="$ROOT/docs/diagrams/src"
OUT="$ROOT/docs/diagrams"
WIDTH=1600

# Light -> dark colour map (DIAGRAM-STYLE palette). Order matters: background first.
to_dark() {
  sed -e 's/#ffffff/#0d1117/g' \
      -e 's/#1f2328/#e6edf3/g' \
      -e 's/#eef2ff/#1e1b4b/g' \
      -e 's/#4f46e5/#818cf8/g' \
      -e 's/#b91c1c/#f87171/g'
}

for src in "$SRC"/*.dot; do
  [ -e "$src" ] || continue
  name=$(basename "$src" .dot)
  dot -Tsvg "$src" -o "$OUT/$name.svg"
  to_dark < "$src" | dot -Tsvg -o "$OUT/$name-dark.svg"
done

for spec in "$SRC"/*.json; do
  [ -e "$spec" ] || continue
  name=$(basename "$spec" .json)
  python3 "$SRC/sequence.py" "$spec" "$OUT/$name.svg" --theme light
  python3 "$SRC/sequence.py" "$spec" "$OUT/$name-dark.svg" --theme dark
done

for svg in "$OUT"/*.svg; do
  rsvg-convert -w "$WIDTH" "$svg" -o "${svg%.svg}.png"
done

echo "rendered: $(ls "$OUT"/*.svg | wc -l | tr -d ' ') SVG files in docs/diagrams"
