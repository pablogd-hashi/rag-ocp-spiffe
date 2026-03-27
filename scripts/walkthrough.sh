#!/usr/bin/env bash
# Interactive demo walkthrough — runs canned queries with a pause between each.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
API_URL="${API_URL:-http://localhost:8000}"

BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
RESET="\033[0m"

banner() {
  echo ""
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${BOLD}  $1${RESET}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
}

pause() {
  echo -e "${DIM}  Press Enter to continue...${RESET}"
  read -r
}

# ── Wait for query service ──────────────────────────────────────────────
echo -e "${DIM}Waiting for query service...${RESET}"
for i in $(seq 1 30); do
  if curl -sf "${API_URL}/health" >/dev/null 2>&1; then break; fi
  sleep 1
done

banner "RAG Platform — Interactive Demo"

echo "  This walkthrough runs six queries against the indexed"
echo "  platform documents and shows retrieval scores + citations."
echo ""
echo "  The first three hit the original sample docs."
echo "  The last three prove the RAG learned the Vault Scaling Guide."
echo ""
pause

# ── Original corpus ─────────────────────────────────────────────────────
banner "1/6  Consul Leader Election"
"${DIR}/ask.sh" "Which runbook covers a Consul leader election failure and what are the recovery cases?"
pause

banner "2/6  Vault PKI Policy Paths"
"${DIR}/ask.sh" "What Vault paths does the PKI policy allow certificate issuance on?"
pause

banner "3/6  Nomad Ingest Job Credentials"
"${DIR}/ask.sh" "How does the Nomad ingest job get its Qdrant credentials?"
pause

# ── Vault Scaling Guide content ─────────────────────────────────────────
banner "4/6  PKI no_store Performance (from Scaling Guide)"
"${DIR}/ask.sh" "What performance improvement does setting no_store=true give for PKI certificate issuance?"
pause

banner "5/6  Performance Replication WAL Metrics (from Scaling Guide)"
"${DIR}/ask.sh" "What metrics should I monitor to detect replication lag between Vault primary and secondary clusters?"
pause

banner "6/6  Transit vs Transform (from Scaling Guide)"
"${DIR}/ask.sh" "What is the difference between format preserving encryption and tokenization in Vault Transform?"
pause

# ── Done ────────────────────────────────────────────────────────────────
banner "Demo Complete"
echo "  Streamlit UI  → http://localhost:8501"
echo "  Query API     → http://localhost:8000/docs"
echo ""
echo "  Try your own:  task ask -- \"your question here\""
echo ""
