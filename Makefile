# slack-cleansing — convenience wrapper around the Docker image.
#
# Usage examples:
#   make build
#   make scan CHANNEL=random
#   make scan CHANNEL=random SINCE=2024-01-15
#   make dry-run CHANNEL=random
#   make delete CHANNEL=random
#   make scan DM=U0123ABCD               # your DM with this user instead of a channel
#   make dry-run DM=U0123ABCD
#   make delete DM=U0123ABCD
#   make delete DM=U0123ABCD TILL=30d    # keep the last 30 days
#   make scan CHANNEL=random THREAD=1700000000.000100  # just one thread
#   make delete CHANNEL=random THREAD=1700000000.000100
#   make shell                           # drop into the container for debugging
#
# Variables (override on the command line, e.g. `make scan CHANNEL=foo`):
#   IMAGE       Docker image tag                 (default: slack-cleansing)
#   ENV_FILE    Path to env file                 (default: .env)
#   CHANNEL     Channel name; scan writes and delete reads <channel>.jsonl
#   DM          User ID (e.g. U0123ABCD) whose DM to use instead of a channel;
#               scan writes and delete reads dm-<DM>.jsonl. Takes precedence
#               over CHANNEL.
#   THREAD      Thread ts (or p-number from its link) within CHANNEL / DM;
#               scan writes and delete reads <channel>-thread-<ts>.jsonl
#   SINCE       --since value for scan           (default: $SLACK_SINCE)
#   TILL        only messages before this date, or N days ago like 30d;
#               for scan, dry-run, delete        (default: $SLACK_TILL)
#   YES         set to 1 to add --yes            (skip confirmation prompt)

IMAGE     ?= slack-cleansing
ENV_FILE  ?= .env

DOCKER_RUN = docker run --rm -it \
	--env-file $(ENV_FILE) \
	-v "$(CURDIR):/work" \
	--user "$(shell id -u):$(shell id -g)" \
	$(IMAGE)

# Optional flags assembled from variables.
YES_FLAG        := $(if $(YES),--yes)
SINCE_FLAG      := $(if $(SINCE),--since $(SINCE))
TILL_FLAG       := $(if $(TILL),--till $(TILL))
TARGET_FLAG     := $(if $(DM),--dm "$(DM)",$(if $(CHANNEL),--channel $(CHANNEL)))
THREAD_FLAG     := $(if $(THREAD),--thread $(THREAD))

.PHONY: help build scan dry-run delete shell clean

help:
	@awk 'BEGIN{FS=":.*##"; printf "Targets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: CHANNEL=... SINCE=... TILL=... DM=U... THREAD=ts YES=1"

build: ## Build the Docker image
	docker build -t $(IMAGE) .

scan: ## Scan a channel or DM. CHANNEL=name or DM=userID. Optional: THREAD= SINCE= TILL=
	$(DOCKER_RUN) scan $(TARGET_FLAG) $(THREAD_FLAG) $(SINCE_FLAG) $(TILL_FLAG)

dry-run: ## Preview deletes without touching Slack. CHANNEL=name or DM=userID [THREAD=] [TILL=]
	$(DOCKER_RUN) delete $(TARGET_FLAG) $(THREAD_FLAG) --dry-run $(TILL_FLAG)

delete: ## Delete messages. CHANNEL=name or DM=userID [THREAD=] [TILL=] [YES=1]
	$(DOCKER_RUN) delete $(TARGET_FLAG) $(THREAD_FLAG) $(TILL_FLAG) $(YES_FLAG)

shell: ## Open a shell in the image (debugging)
	docker run --rm -it --env-file $(ENV_FILE) -v "$(CURDIR):/work" \
		--user "$(shell id -u):$(shell id -g)" --entrypoint /bin/bash $(IMAGE)

clean: ## Remove the Docker image
	docker rmi $(IMAGE) 2>/dev/null || true
