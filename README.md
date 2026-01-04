# subsmith

Batchable WhisperX pipeline with cached models, Terraform + Makefile automation, and optional remote-to-EC2 transfer helpers.

## Credits
- Built on [WhisperX](https://github.com/m-bain/whisperx)
- Uses Hugging Face models (`openai/whisper-large-v3(-turbo)`), alignment models via WhisperX, AWS EC2 + S3 for caching.

## Philosophy
- The Makefile is the living reference: every common action is a target, with `##` docs visible via `make help`.
- Defaults are safe placeholders. Override via CLI vars or a local `.env.local` 
- Keep secrets out of git; use `make scan-for-secrets` (trufflehog) before committing.

## Prereqs
- Docker (+ buildx), Python 3.10+, awscli, terraform/tofu, trufflehog (optional), ffmpeg on the host, and nvidia drivers on the GPU host.
- AWS creds configured on the laptop for infra and cache sync; no creds on the Pi.

## Quickstart
1) Copy `.env.local.example` to `.env.local` and set your values (docker repo, HF bucket, optional audio bucket, git repo, optional remote hosts/CIDRs, etc.).
2) Build/push image: `make push` (uses DOCKER_REPO).
3) Provision GPU box: `make tf-apply` (SSH opened to your IP; add other ingress via `make gen-ssh-cidrs REMOTE_IP_HOSTS="host1 host2"`).
4) Seed HF cache (optional/local): `make seed-hf-cache` (downloads Whisper models + aligners; syncs to S3 if HF_BUCKET set).
5) If you extract audio on another machine: `python3 scripts/extract_missing_audio.py /path/to/media --out /path/to/outdir` there, then `make rsync-pi-to-ec2 PI_HOST=remotehost PI_SRC=/path/to/outdir/ EC2_HOST=ec2-user@your-ec2` from your control machine. This step is optional—skip if you process directly on the GPU box.
6) On the GPU host: run containerized batch (mount `/media`, `/opt/hf-cache`, and `/opt/audio`), e.g. `make docker-batch IMAGE=docker.io/you/whisperx:latest` (or `make batch` locally if you have a GPU).

## Key Make targets (see `make help`)
- `build`, `push` (set DOCKER_REPO)
- `seed-hf-cache`: prefetch Whisper models + alignment for en/de/es/fr/ru/ja
- `tf-apply`, `tf-destroy`: bring up/tear down EC2 + S3 cache wiring
- `gen-ssh-cidrs`: collect public IPs from remote hosts for SG allowlist
- `pi-ssh`, `rsync-pi-to-ec2`: move extracted audio to EC2 via SSH (remote host optional)
- `langs`, `extract`, `batch`, `docker-batch`: local/container runs
- `scan-for-secrets`: run trufflehog on git history

## Notes
- HF cache and media are ignored; keep `.env.local` out of git. Optional `AUDIO_BUCKET` syncs to `/opt/audio` and is mounted into the container.
- Default ASR model: `large-v3-turbo`; adjust via `ASR_MODEL`/`ASR_BATCH_SIZE`.
- Alignment prefetch covers en/de/es/fr/ru/ja; WhisperX will auto-fetch others as needed.
- EC2 key path defaults to `~/.ssh/id_rsa.pub`; override in Terraform if different.

## License
- Your choice; remember WhisperX is MPL-2.0.
