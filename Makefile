.DEFAULT_GOAL := help
-include .env.local

# Makefile for WhisperX workflow (acts as living docs). Override vars on the CLI, e.g. make push REGION=us-west-2 TAG=latest

REGION ?= us-east-1
DOCKER_REPO ?= docker.io/scraun/subsmith
DOCKER_USER ?=
DOCKER_PASS ?=
PLATFORM ?= linux/amd64
TAG ?= latest
IMAGE ?= $(DOCKER_REPO):$(TAG)
AUDIO_DIR ?= audio
OUT_DIR ?= srts
ROOT ?= .
HF_BUCKET ?= whisper-hf-cache-static
AUDIO_BUCKET ?=
CACHE_DIR ?= ./hf-cache
PREFIX ?=
ASR_MODEL ?= large-v3-turbo
ASR_BATCH_SIZE ?= 8
RUNTIME_INSTANCE_TYPE ?= g6.xlarge
EXTRA_SSH_CIDRS ?= []
GIT_REPO ?= 
GIT_BRANCH ?= main
REMOTE_IP_HOSTS ?=
PI_HOST ?=
PI_SRC ?= /home/pi/extracted_audio/
EC2_DEST ?= /home/ec2-user/audio/
EC2_HOST ?= $(shell if [ -f ssh_target.txt ]; then h=$$(cat ssh_target.txt); if echo $$h | grep -q '@'; then echo $$h; else echo ec2-user@$$h; fi; fi)
TF_CMD ?= tofu

.PHONY: help venv 
.PHONY: login-docker build push prune 
.PHONY: langs extract batch docker-batch 
.PHONY: create-hf-bucket destroy-hf-bucket 
.PHONY: seed-hf-cache 
.PHONY: tf-apply tf-destroy
.PHONY: gen-ssh-cidrs pi-ssh rsync-pi-to-ec2
.PHONY: scan-for-secrets

help: ## List targets and usage
	@printf "WhisperX workflow targets (override vars like REGION/TAG/DOCKER_REPO on the CLI)\n\n"
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/: /' | column -s ':' -t

venv: ## Reminder: activate local venv (uses venv310 by default)
	@echo "Run: source venv310/bin/activate" && echo "If missing, create: python3 -m venv venv310 && source venv310/bin/activate"

login-docker: ## Log in to your registry (uses DOCKER_USER/DOCKER_PASS if set, else interactive)
	@if [ -n "$(DOCKER_PASS)" ] && [ -n "$(DOCKER_USER)" ]; then \
		echo "$(DOCKER_PASS)" | docker login --username $(DOCKER_USER) --password-stdin $(DOCKER_REGISTRY); \
	else \
		docker login $(DOCKER_REGISTRY); \
	fi

build: ## Build image and tag for the repo
	docker buildx build --squash --platform $(PLATFORM) --pull -t $(DOCKER_REPO):$(TAG) .

push: build login-docker ## Build and push to the repo
	@if [ -z "$(DOCKER_REPO)" ]; then echo "Set DOCKER_REPO"; exit 1; fi
	docker push $(DOCKER_REPO):$(TAG)

prune: ## Prune dangling Docker images/containers
	docker system prune -af

langs: ## List audio/subtitle languages under ROOT (uses scripts/list_media_langs.py)
	python3 scripts/list_media_langs.py $(ROOT)

extract: ## Extract audio lacking matching subtitles into AUDIO_DIR (scripts/extract_missing_audio.py)
	python3 scripts/extract_missing_audio.py $(ROOT) --out $(AUDIO_DIR)

batch: ## Run WhisperX locally over AUDIO_DIR/*.mka into OUT_DIR (CPU/GPU as available)
	@mkdir -p $(OUT_DIR)
	for f in $(AUDIO_DIR)/*.mka; do \
		[ -f "$$f" ] || continue; \
		python3 scripts/run_whisperx.py "$$f" --model $(ASR_MODEL) --batch-size $(ASR_BATCH_SIZE) --output $(OUT_DIR)/$$(basename "$${f%.*}").srt; \
	done

docker-batch: ## Run WhisperX in container on GPU host (uses /opt/hf-cache for reuse)
	for f in /opt/audio/*.mka; do \
		[ -f "$$f" ] || continue; \
		base=$${f%.mka}; \
		srt1=$${base}.srt; srt2=$${base}.whisperx.srt; \
		if [ -f "$$srt1" ] || [ -f "$$srt2" ]; then echo "Skipping $$f (SRT exists)"; continue; fi; \
		docker run --gpus all --rm \
			-e HF_HOME=/opt/hf-cache/hf \
			-e TRANSFORMERS_CACHE=/opt/hf-cache/hf \
			-e TORCH_HOME=/opt/hf-cache/torch \
			-e XDG_CACHE_HOME=/opt/hf-cache/xdg \
			-v /opt/hf-cache:/opt/hf-cache \
			-v /opt/audio:/opt/audio \
			-v /media:/media \
			$(IMAGE) "$$f" --output_dir /opt/audio/ --model $(ASR_MODEL) --batch_size $(ASR_BATCH_SIZE) --compute_type float16 --device cuda --vad_method silero; \
	done

create-hf-bucket: ## Create HF cache bucket (blocks public access, AES256 SSE); set HF_BUCKET/REGION
	@test -n "$(HF_BUCKET)" || (echo "HF_BUCKET is required" && exit 1)
	@if [ "$(REGION)" = "us-east-1" ]; then \
		aws s3api create-bucket --bucket $(HF_BUCKET) --region $(REGION) >/dev/null; \
	else \
		aws s3api create-bucket --bucket $(HF_BUCKET) --region $(REGION) --create-bucket-configuration LocationConstraint=$(REGION) >/dev/null; \
	fi
	aws s3api put-public-access-block --bucket $(HF_BUCKET) --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null
	aws s3api put-bucket-encryption --bucket $(HF_BUCKET) --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
	@echo "Created bucket: $(HF_BUCKET)"

destroy-hf-bucket: ## Destroy HF cache bucket (force); set HF_BUCKET
	@test -n "$(HF_BUCKET)" || (echo "HF_BUCKET is required" && exit 1)
	aws s3 rb s3://$(HF_BUCKET) --force
	@echo "Deleted bucket: $(HF_BUCKET)"

seed-hf-cache: ## Prefetch Whisper models + aligners into CACHE_DIR; optional sync to S3 (HF_BUCKET/PREFIX)
	@test -n "$(HF_BUCKET)" || (echo "HF_BUCKET is required" && exit 1)
	@if [ -n "$(PREFIX)" ]; then PREFIX_ARG="--prefix $(PREFIX)"; else PREFIX_ARG=""; fi; \
	python3 scripts/seed_hf_cache.py --cache-dir $(CACHE_DIR) --bucket $(HF_BUCKET) $$PREFIX_ARG

## runs tofu init then tofu apply with hf_cache_bucket and docker_repo set from the Make vars,
## creating the VPC/SG/instance/IAM/etc. (ephemeral infra).
tf-apply: 
	$(TF_CMD) init
	$(TF_CMD) apply -auto-approve \
		-var "hf_cache_bucket=$(HF_BUCKET)" \
		-var "audio_bucket=$(AUDIO_BUCKET)" \
		-var "docker_repo=$(DOCKER_REPO)" \
		-var "runtime_instance_type=$(RUNTIME_INSTANCE_TYPE)" \
		-var "extra_ssh_cidrs=$(EXTRA_SSH_CIDRS)" \
		-var "git_repo=$(GIT_REPO)" \
		-var "git_branch=$(GIT_BRANCH)"

tf-destroy: ## destroy ephemeral infra created by tf-apply
	$(TF_CMD) destroy -auto-approve 

gen-ssh-cidrs: ## Derive EXTRA_SSH_CIDRS via ssh to REMOTE_IP_HOSTS (runs checkip on each)
	@test -n "$(REMOTE_IP_HOSTS)" || { echo "Set REMOTE_IP_HOSTS=\"host1 host2\""; exit 1; }
	IPS=""; \
	for h in $(REMOTE_IP_HOSTS); do \
		ip=$$(ssh $$h "curl -s https://checkip.amazonaws.com | tr -d '\n'" 2>/dev/null); \
		if [ -z "$$ip" ]; then echo "Failed to get IP from $$h"; exit 1; fi; \
		IPS="$$IPS\"$$ip/32\","; \
	done; \
	echo EXTRA_SSH_CIDRS=[$${IPS%,}]

pi-ssh: ## SSH into the Pi (uses PI_HOST; relies on your ssh config)
	@test -n "$(PI_HOST)" || { echo "Set PI_HOST"; exit 1; }
	ssh $(PI_HOST)

rsync-pi-to-ec2: ## Rsync extracted audio from Pi to EC2 (set PI_HOST/PI_SRC/EC2_HOST/EC2_DEST)
	@test -n "$(PI_HOST)" || { echo "Set PI_HOST"; exit 1; }
	@test -n "$(PI_SRC)" || { echo "Set PI_SRC"; exit 1; }
	@test -n "$(EC2_HOST)" || { echo "Set EC2_HOST (or ensure ssh_target.txt exists)"; exit 1; }
	@test -n "$(EC2_DEST)" || { echo "Set EC2_DEST"; exit 1; }
	rsync -av --progress $(PI_SRC) $(EC2_HOST):$(EC2_DEST)

scan-for-secrets: ## Run trufflehog against the git history (requires trufflehog installed)
	trufflehog git file://$(PWD)
