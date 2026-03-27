#!/usr/bin/env bash
# Quick CLI query against the running query service.
# Usage: ./scripts/ask.sh "your question here"
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
QUESTION="${1:?Usage: ask.sh \"your question\"}"

RESP=$(curl -sf "${API_URL}/ask" \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"${QUESTION}\"}")

ANSWER=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['answer'])")
SCORE=$(echo "$RESP"  | python3 -c "import sys,json; print(json.load(sys.stdin)['top_score'])")
SOURCES=$(echo "$RESP" | python3 -c "
import sys,json
for s in json.load(sys.stdin)['sources']:
    print(f'  • {s}')
")

# Colour the score
if python3 -c "exit(0 if ${SCORE} >= 0.5 else 1)" 2>/dev/null; then
  SCORE_CLR="\033[32m${SCORE}\033[0m"  # green
else
  SCORE_CLR="\033[33m${SCORE}\033[0m"  # orange/yellow
fi

echo ""
echo -e "\033[1mQ:\033[0m ${QUESTION}"
echo ""
echo -e "\033[1mA:\033[0m ${ANSWER}"
echo ""
echo -e "\033[1mScore:\033[0m ${SCORE_CLR}"
echo ""
echo -e "\033[1mSources:\033[0m"
echo "$SOURCES"
echo ""
