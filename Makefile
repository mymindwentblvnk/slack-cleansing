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
#   make scan DM=U0123ABCD               # your DM with this user instead of a channel
#   make dry-run DM=U0123ABCD
#   make delete DM=U0123ABCD
#   make delete DM=U0123ABCD TILL=30d    # keep the last 30 days
#   make stats                           # your message count per channel / DM
#   make stats TOP=0 SINCE=2024-01-01    # all conversations, since a date
#   make shell                           # drop into the container for debugging
#
# Variables (override on the command line, e.g. `make scan CHANNEL=foo`):
#   IMAGE       Docker image tag                 (default: slack-delete)
#   ENV_FILE    Path to env file                 (default: .env)
#   CHANNEL     Channel name; scan writes and delete reads <channel>.jsonl
#                                                (default: $SLACK_CHANNEL from .env)
#   DM          User ID (e.g. U0123ABCD) whose DM to use instead of a channel;
#               scan writes and delete reads dm-<DM>.jsonl. Takes precedence
#               over CHANNEL.
#   SINCE       --since value for scan, stats    (default: $SLACK_SINCE)
#   TILL        only messages before this date, or N days ago like 30d;
#               for scan, dry-run, delete, stats (default: $SLACK_TILL)
#   FULL        set to 1 to add --full           (read the whole channel history)
#   DELAY       --delay value for delete         (default: 1.1-1.4)
#   SKIP_MEDIA  set to 1 to add --skip-media
#   YES         set to 1 to add --yes            (skip confirmation prompt)
#   TOP         --top value for stats            (default: 30, 0 = all)

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
TILL_FLAG       := $(if $(TILL),--till $(TILL))
DELAY_FLAG      := $(if $(DELAY),--delay $(DELAY))
TARGET_FLAG     := $(if $(DM),--dm "$(DM)",$(if $(CHANNEL),--channel $(CHANNEL)))
FULL_FLAG       := $(if $(FULL),--full)
TOP_FLAG        := $(if $(TOP),--top $(TOP))

.PHONY: help build scan dry-run delete stats shell clean

help:
	@awk 'BEGIN{FS=":.*##"; printf "Targets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: CHANNEL=... SINCE=... TILL=... DM=U... FULL=1 DELAY=... SKIP_MEDIA=1 YES=1 TOP=..."

build: ## Build the Docker image
	docker build -t $(IMAGE) .

scan: ## Scan a channel or DM. CHANNEL=name or DM=userID (or SLACK_CHANNEL in .env). Optional: SINCE= TILL= FULL=1
	$(DOCKER_RUN) scan $(TARGET_FLAG) $(SINCE_FLAG) $(TILL_FLAG) $(FULL_FLAG)

dry-run: ## Preview deletes without touching Slack. CHANNEL=name or DM=userID [TILL=] [SKIP_MEDIA=1]
	$(DOCKER_RUN) delete $(TARGET_FLAG) --dry-run $(TILL_FLAG) $(SKIP_MEDIA_FLAG)

delete: ## Delete messages. CHANNEL=name or DM=userID [TILL=] [SKIP_MEDIA=1] [YES=1] [DELAY=1.5-3]
	$(DOCKER_RUN) delete $(TARGET_FLAG) $(TILL_FLAG) $(SKIP_MEDIA_FLAG) $(YES_FLAG) $(DELAY_FLAG)

stats: ## List where you've posted, most messages first. Optional: TOP=30 (0 = all) SINCE= TILL=
	$(DOCKER_RUN) stats $(TOP_FLAG) $(SINCE_FLAG) $(TILL_FLAG)

shell: ## Open a shell in the image (debugging)
	docker run --rm -it --env-file $(ENV_FILE) -v "$(CURDIR):/work" \
		--user "$(shell id -u):$(shell id -g)" --entrypoint /bin/bash $(IMAGE)

clean: ## Remove the Docker image
	docker rmi $(IMAGE) 2>/dev/null || true
