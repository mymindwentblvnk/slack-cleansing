# slack-delete — convenience wrapper around the Docker image.
#
# Usage examples:
#   make build
#   make scan                            # uses SLACK_CHANNEL from .env
#   make scan CHANNEL=random
#   make scan CHANNEL=random SINCE=2024-01-15
#   make dry-run FILE=random.jsonl SKIP_MEDIA=1
#   make delete FILE=random.jsonl SKIP_MEDIA=1
#   make shell                           # drop into the container for debugging
#
# Variables (override on the command line, e.g. `make scan CHANNEL=foo`):
#   IMAGE       Docker image tag                 (default: slack-delete)
#   ENV_FILE    Path to env file                 (default: .env)
#   CHANNEL     Channel name for scan            (default: $SLACK_CHANNEL from .env)
#   FILE        JSONL path for delete            (required for those targets)
#   SINCE       --since value for scan           (default: $SLACK_SINCE)
#   DELAY       --delay value for delete         (default: 1.1-1.4)
#   SKIP_MEDIA  set to 1 to add --skip-media
#   YES         set to 1 to add --yes            (skip confirmation prompt)

IMAGE     ?= slack-delete
ENV_FILE  ?= .env

DOCKER_RUN = docker run --rm -it \
	--env-file $(ENV_FILE) \
	-v "$(CURDIR):/work" \
	--user "$(shell id -u):$(shell id -g)" \
	$(IMAGE)

# Optional flags assembled from variables.
SKIP_MEDIA_FLAG := $(if $(SKIP_MEDIA),--skip-media)
YES_FLAG        := $(if $(YES),--yes)
SINCE_FLAG      := $(if $(SINCE),--since $(SINCE))
DELAY_FLAG      := $(if $(DELAY),--delay $(DELAY))
CHANNEL_FLAG    := $(if $(CHANNEL),--channel $(CHANNEL))

.PHONY: help build scan dry-run delete shell clean

help:
	@awk 'BEGIN{FS=":.*##"; printf "Targets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: CHANNEL=... FILE=... SINCE=... DELAY=... SKIP_MEDIA=1 YES=1"

build: ## Build the Docker image
	docker build -t $(IMAGE) .

scan: ## Scan a channel. CHANNEL=name (or SLACK_CHANNEL in .env). Optional: SINCE=
	$(DOCKER_RUN) scan $(CHANNEL_FLAG) $(SINCE_FLAG)

dry-run: ## Preview deletes without touching Slack. FILE=path.jsonl [SKIP_MEDIA=1]
	@test -n "$(FILE)" || (echo "ERROR: FILE=path.jsonl is required" >&2; exit 2)
	$(DOCKER_RUN) delete --file $(FILE) --dry-run $(SKIP_MEDIA_FLAG)

delete: ## Delete messages. FILE=path.jsonl [SKIP_MEDIA=1] [YES=1] [DELAY=1.5-3]
	@test -n "$(FILE)" || (echo "ERROR: FILE=path.jsonl is required" >&2; exit 2)
	$(DOCKER_RUN) delete --file $(FILE) $(SKIP_MEDIA_FLAG) $(YES_FLAG) $(DELAY_FLAG)

shell: ## Open a shell in the image (debugging)
	docker run --rm -it --env-file $(ENV_FILE) -v "$(CURDIR):/work" \
		--user "$(shell id -u):$(shell id -g)" --entrypoint /bin/bash $(IMAGE)

clean: ## Remove the Docker image
	docker rmi $(IMAGE) 2>/dev/null || true
