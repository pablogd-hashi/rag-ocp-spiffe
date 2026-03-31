#!/usr/bin/env bash
# Interactive demo — guided tour or free-form Q&A against the indexed docs.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
API_URL="${API_URL:-http://localhost:8000}"

BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
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
printf "${DIM}Waiting for query service...${RESET}"
for i in $(seq 1 30); do
  if curl -sf "${API_URL}/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
echo ""

banner "RAG Platform — Interactive Demo"

echo -e "  Indexed documents:"
echo -e "  ${DIM}runbooks/${RESET}      Consul leader election, Vault key mgmt, PKI scaling,"
echo -e "  ${DIM}               ${RESET}performance replication, Transit vs Transform"
echo -e "  ${DIM}policies/${RESET}      Vault PKI HCL policy"
echo -e "  ${DIM}architecture/${RESET}  Consul Connect + SPIFFE, OpenShift deployment"
echo -e "  ${DIM}configuration/${RESET} CRC prerequisites, Vault PKI + Consul Connect setup"
echo -e "  ${DIM}jobs/${RESET}          Kubernetes ingest Job (Consul Connect mTLS)"
echo ""
echo -e "  ${GREEN}UI also available at → http://localhost:8501${RESET}"
echo ""

echo -e "  ${BOLD}Choose a mode:${RESET}"
echo "  1) Guided tour  — 6 questions covering the full corpus"
echo "  2) Ask your own — free-form Q&A (type 'quit' to exit)"
echo "  3) Both"
echo ""
printf "  Choice [1]: "
read -r MODE
MODE="${MODE:-1}"

# ── Guided tour ─────────────────────────────────────────────────────────
run_guided() {
  banner "1/6  Consul leader election"
  "${DIR}/ask.sh" "What are the recovery steps when Consul loses quorum during a leader election?"
  pause

  banner "2/6  Vault PKI as Consul Connect CA"
  "${DIR}/ask.sh" "How does Consul Connect use Vault PKI to issue SPIFFE certificates instead of its built-in CA?"
  pause

  banner "3/6  SPIFFE identities"
  "${DIR}/ask.sh" "What SPIFFE identity format does Consul Connect assign to services in the mesh?"
  pause

  banner "4/6  Vault key rotation"
  "${DIR}/ask.sh" "What is the procedure for rotating the Vault master key and what are the risks?"
  pause

  banner "5/6  Transit vs Transform"
  "${DIR}/ask.sh" "What is the difference between Vault Transit encryption and Transform tokenization?"
  pause

  banner "6/6  Service Intentions"
  "${DIR}/ask.sh" "How do Service Intentions enforce deny-by-default between services in the mesh?"
  pause
}

# ── Free-form Q&A ────────────────────────────────────────────────────────
run_freeform() {
  banner "Ask your own questions"
  echo -e "  ${DIM}Type 'quit' or press Enter on an empty line to exit.${RESET}"
  echo ""
  echo -e "  ${DIM}Things worth asking:${RESET}"
  echo -e "  ${DIM}  • What Vault paths does the PKI policy allow for certificate issuance?${RESET}"
  echo -e "  ${DIM}  • What is the difference between connect_root and connect_inter in Vault?${RESET}"
  echo -e "  ${DIM}  • How does Consul authenticate to Vault to sign SPIFFE certificates?${RESET}"
  echo -e "  ${DIM}  • What happens to PKI performance when the no_store option is enabled?${RESET}"
  echo -e "  ${DIM}  • What metrics should I monitor for Vault replication lag?${RESET}"
  echo -e "  ${DIM}  • What CRC resource requirements does this platform need to run?${RESET}"
  echo ""

  while true; do
    printf "${BOLD}Q:${RESET} "
    read -r QUESTION
    [[ -z "$QUESTION" || "$QUESTION" == "quit" || "$QUESTION" == "exit" ]] && break
    "${DIR}/ask.sh" "$QUESTION"
    echo ""
  done
}

case "$MODE" in
  1) run_guided ;;
  2) run_freeform ;;
  3) run_guided; run_freeform ;;
  *) run_guided ;;
esac

banner "Done"
echo -e "  ${GREEN}Streamlit UI${RESET}  → http://localhost:8501"
echo -e "  ${DIM}task ask -- \"your question\"  to ask a single question from the CLI${RESET}"
echo ""
