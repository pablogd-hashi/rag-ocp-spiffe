.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: setup ingest up down demo clean help

setup: ## Start Ollama and pull required models
	$(COMPOSE) up -d ollama
	@echo "Waiting for Ollama to be ready..."
	@for i in $$(seq 1 30); do \
		if docker compose exec ollama ollama list >/dev/null 2>&1; then break; fi; \
		sleep 2; \
	done
	docker compose exec ollama ollama pull nomic-embed-text
	docker compose exec ollama ollama pull mistral
	@echo "✓ Models ready"

ingest: ## Run the ingest pipeline against ./docs
	$(COMPOSE) run --rm ingest

up: ## Start qdrant, ollama, query-service, and UI
	$(COMPOSE) up -d qdrant ollama query-service ui
	@echo ""
	@echo "Services:"
	@echo "  Qdrant     → http://localhost:6333"
	@echo "  Query API  → http://localhost:8000"
	@echo "  Streamlit  → http://localhost:8501"

down: ## Stop all services
	$(COMPOSE) down

demo: setup ingest up ## Full first-time experience (setup → ingest → up)

clean: ## Stop services and remove volumes
	$(COMPOSE) down -v
	@echo "✓ Volumes removed"

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
