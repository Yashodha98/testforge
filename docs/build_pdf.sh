#!/bin/sh
# Re-builds technical_evidence.pdf from technical_evidence.html (macOS, Google Chrome)
cd "$(dirname "$0")"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="technical_evidence.pdf" "file://$(pwd)/technical_evidence.html" 2>/dev/null
echo "Wrote $(pwd)/technical_evidence.pdf"
