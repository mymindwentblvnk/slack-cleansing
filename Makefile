# slack-delete — convenience wrapper around the Docker image.
#
# Usage examples:
#   make build
#   make scan                            # uses SLACK_CHANNEL from .env
#   make scan CHANNEL=random
#   make scan CHANNEL=random SINCE=2024-01-15
#   make scan CHANNEL=random FULL=1      # slow: read the whole channel history
#   make dry-run CHANNEL=random SKIP_MEDIA=1
#   make delete CHANNEL=random SKIP_MEDIA=1
#   make shell                           # drop into the container for debugging
#
# Variables (override on the command line, e.g. `make scan CHANNEL=foo`):
#   IMAGE       Docker image tag                 (default: slack-delete)
#   ENV_FILE    Path to env file                 (default: .env)
#   CHANNEL     Channel name; scan writes and delete reads <channel>.jsonl
#                                                (default: $SLACK_CHANNEL from .env)
#   SINCE       --since value for scan           (default: $SLACK_SINCE)
#   FULL        set to 1 to add --full           (read the whole channel history)
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
FULL_FLAG       := $(if $(FULL),--full)

.PHONY: help build scan dry-run delete shell clean

help:
	@awk 'BEGIN{FS=":.*##"; printf "Targets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: CHANNEL=... SINCE=... FULL=1 DELAY=... SKIP_MEDIA=1 YES=1"

build: ## Build the Docker image
	docker build -t $(IMAGE) .

scan: ## Scan a channel. CHANNEL=name (or SLACK_CHANNEL in .env). Optional: SINCE= FULL=1
	$(DOCKER_RUN) scan $(CHANNEL_FLAG) $(SINCE_FLAG) $(FULL_FLAG)

dry-run: ## Preview deletes without touching Slack. CHANNEL=name [SKIP_MEDIA=1]
	$(DOCKER_RUN) delete $(CHANNEL_FLAG) --dry-run $(SKIP_MEDIA_FLAG)

delete: ## Delete messages. CHANNEL=name [SKIP_MEDIA=1] [YES=1] [DELAY=1.5-3]
	$(DOCKER_RUN) delete $(CHANNEL_FLAG) $(SKIP_MEDIA_FLAG) $(YES_FLAG) $(DELAY_FLAG)

shell: ## Open a shell in the image (debugging)
	docker run --rm -it --env-file $(ENV_FILE) -v "$(CURDIR):/work" \
		--user "$(shell id -u):$(shell id -g)" --entrypoint /bin/bash $(IMAGE)

clean: ## Remove the Docker image
	docker rmi $(IMAGE) 2>/dev/null || true
