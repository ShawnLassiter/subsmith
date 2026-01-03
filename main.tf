terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "aws_region" "current" {}

variable "hf_cache_bucket" {
  description = "S3 bucket name for HF cache sync"
  type        = string
  default     = "whisper-hf-cache-static"
}

variable "dockerhub_repo" {
  description = "Docker Hub repository (e.g., docker.io/youruser/whisperx)"
  type        = string
  default     = ""
}

variable "ecr_repo_url" {
  description = "Full ECR repository URL (e.g., 123456789012.dkr.ecr.us-east-1.amazonaws.com/whisperx). Leave blank if using Docker Hub."
  type        = string
  default     = ""
}

variable "override_ami_id" {
  description = "Optional AMI ID to force instead of baked/latest base. Leave blank to auto-select baked if present."
  type        = string
  default     = ""
}

variable "runtime_instance_type" {
  description = "Instance type used when running from a baked AMI (normal ops)."
  type        = string
  default     = "g6.xlarge"
}

variable "git_repo" {
  description = "Optional git repository URL to clone on the instance."
  type        = string
  default     = ""
}

variable "git_branch" {
  description = "Branch to checkout when cloning git_repo."
  type        = string
  default     = "main"
}

variable "extra_ssh_cidrs" {
  description = "Additional CIDR blocks allowed for SSH (e.g., VPN subnets or static IPs)."
  type        = list(string)
  default     = []
}

# --- 1. NETWORK ---
resource "aws_vpc" "temp_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags = { Name = "whisper-g6-vpc" }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.temp_vpc.id
}

resource "aws_subnet" "temp_subnet" {
  vpc_id                  = aws_vpc.temp_vpc.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true
  availability_zone       = local.selected_az
}

resource "aws_route_table" "rt" {
  vpc_id = aws_vpc.temp_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
}

resource "aws_route_table_association" "a" {
  subnet_id      = aws_subnet.temp_subnet.id
  route_table_id = aws_route_table.rt.id
}

# --- 2. DATA ---
data "http" "myip" {
  url = "https://checkip.amazonaws.com"
}

data "aws_ami" "dl_ami" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-ecs-gpu-hvm-*", "amzn2-ami-ecs-gpu-nvidia*" ]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

# Instance type offering lookup to pick an AZ that supports g6.x
data "aws_ec2_instance_type_offerings" "g6" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = ["g6.2xlarge"]
  }
}

# All available AZs as a fallback
data "aws_availability_zones" "available" {
  state = "available"
}

# Latest baked AMI (optional). If none exist, list is empty and we fall back to base DLAMI.
data "aws_ami_ids" "baked" {
  owners = ["self"]

  filter {
    name   = "tag:Name"
    values = ["whisper-g6-golden"]
  }

  sort_ascending = false
}

locals {
  selected_ami = coalesce(
    var.override_ami_id != "" ? var.override_ami_id : null,
    length(data.aws_ami_ids.baked.ids) > 0 ? data.aws_ami_ids.baked.ids[0] : null,
    data.aws_ami.dl_ami.id
  )

  instance_type  = var.runtime_instance_type

  g6_azs      = data.aws_ec2_instance_type_offerings.g6.locations
  selected_az = length(local.g6_azs) > 0 ? local.g6_azs[0] : data.aws_availability_zones.available.names[0]
}

# --- 3. SECURITY ---
resource "aws_key_pair" "deployer" {
  key_name   = "whisper-g6-key"
  public_key = file("~/.ssh/id_rsa.pub")
}

resource "aws_security_group" "allow_ssh" {
  name   = "whisper_g6_sg"
  vpc_id = aws_vpc.temp_vpc.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = concat(["${chomp(data.http.myip.response_body)}/32"], var.extra_ssh_cidrs)
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# IAM role for pulling from ECR
resource "aws_iam_role" "whisper_ecr_role" {
  name = "whisper-ecr-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
      Effect    = "Allow"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_readonly" {
  role       = aws_iam_role.whisper_ecr_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Inline policy to allow S3 cache sync
data "aws_iam_policy_document" "s3_cache" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket"
    ]

    resources = [
      "arn:aws:s3:::${var.hf_cache_bucket}",
      "arn:aws:s3:::${var.hf_cache_bucket}/*"
    ]
  }
}

resource "aws_iam_role_policy" "s3_cache" {
  name   = "whisper-s3-cache"
  role   = aws_iam_role.whisper_ecr_role.id
  policy = data.aws_iam_policy_document.s3_cache.json
}

resource "aws_iam_instance_profile" "whisper_profile" {
  name = "whisper-ecr-profile"
  role = aws_iam_role.whisper_ecr_role.name
}

# --- 4. THE INSTANCE (On-Demand Version) ---
resource "aws_instance" "whisper_box" {
  ami                    = local.selected_ami
  instance_type          = local.instance_type
  key_name               = aws_key_pair.deployer.key_name
  subnet_id              = aws_subnet.temp_subnet.id
  vpc_security_group_ids = [aws_security_group.allow_ssh.id]
  iam_instance_profile   = aws_iam_instance_profile.whisper_profile.name

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }


  # --- INSTALLATION SCRIPT ---
  user_data = <<-EOF
              #!/bin/bash -xe
              systemctl enable --now docker

              mkdir -p /opt/hf-cache
              chown ec2-user:ec2-user /opt/hf-cache

              if [ -n "${var.hf_cache_bucket}" ]; then
                sudo -u ec2-user aws s3 sync s3://${var.hf_cache_bucket}/ /opt/hf-cache || true
              fi

              if [ -n "${var.dockerhub_repo}" ]; then
                docker pull ${var.dockerhub_repo}:latest
                docker tag ${var.dockerhub_repo}:latest whisperx:latest
              elif [ -n "${var.ecr_repo_url}" ]; then
                aws ecr get-login-password --region ${data.aws_region.current.name} | docker login --username AWS --password-stdin ${var.ecr_repo_url}
                docker pull ${var.ecr_repo_url}:latest
                docker tag ${var.ecr_repo_url}:latest whisperx:latest
              fi

              if [ -n "${var.git_repo}" ]; then
                sudo -u ec2-user bash -lc "\
                  if [ -d /home/ec2-user/subs/.git ]; then \
                    cd /home/ec2-user/subs && git fetch --all && git reset --hard origin/${var.git_branch}; \
                  else \
                    git clone --branch ${var.git_branch} ${var.git_repo} /home/ec2-user/subs; \
                  fi"
              fi

              cat >/usr/local/bin/sync_hf_cache.sh <<'EOSYNC'
              #!/bin/bash
              if [ -n "${var.hf_cache_bucket}" ]; then
                aws s3 sync /opt/hf-cache s3://${var.hf_cache_bucket}/ || true
              fi
              EOSYNC
              chmod +x /usr/local/bin/sync_hf_cache.sh

              cat >/etc/systemd/system/hf-cache-sync.service <<'EOSVC'
              [Unit]
              Description=Sync HF cache to S3 on shutdown
              DefaultDependencies=no
              Before=shutdown.target

              [Service]
              Type=oneshot
              ExecStart=/usr/local/bin/sync_hf_cache.sh

              [Install]
              WantedBy=multi-user.target
              EOSVC

              systemctl enable hf-cache-sync.service

              touch /home/ec2-user/install_complete
              chown ec2-user:ec2-user /home/ec2-user/install_complete
              EOF

  tags = {
    Name = "Whisper-G6-L4-Worker"
  }

  provisioner "remote-exec" {
    inline = [
      "while [ ! -f /home/ec2-user/install_complete ]; do echo 'Waiting for cache sync...'; sleep 10; done",
      "echo 'Ready to Transcribe!'"
    ]

    connection {
      type        = "ssh"
      user        = "ec2-user"
      private_key = file("~/.ssh/id_rsa")
      host        = self.public_ip
    }
  }
}

# --- 4b. GOLDEN AMI FROM BUILT INSTANCE ---
resource "aws_ami_from_instance" "whisper_golden" {
  # Run `tofu apply -target=aws_ami_from_instance.whisper_golden` after the box is fully provisioned.
  name                    = "whisper-g6-${formatdate("YYYYMMDD-hhmm", timestamp())}"
  source_instance_id      = aws_instance.whisper_box.id
  snapshot_without_reboot = true

  tags = {
    Name = "whisper-g6-golden"
  }
}

# Persist the SSH target for downstream tools
resource "local_file" "ssh_target" {
  content  = "ubuntu@${aws_instance.whisper_box.public_dns}"
  filename = "${path.module}/ssh_target.txt"
}

# --- 5. OUTPUTS ---
output "ssh_command" {
  value = "ssh -i ~/.ssh/id_rsa ec2-user@${aws_instance.whisper_box.public_dns}"
}

output "upload_command" {
  value = "scp -i ~/.ssh/id_rsa YOUR_FILE.mp3 ec2-user@${aws_instance.whisper_box.public_dns}:/home/ec2-user/"
}

output "run_command" {
  value = "docker run --gpus all --rm -v /opt/hf-cache:/opt/hf-cache -v /home/ec2-user:/data whisperx:latest /data/YOUR_FILE.mka --model large-v2 --compute_type float16 --device cuda"
}

output "golden_ami_id" {
  value = aws_ami_from_instance.whisper_golden.id
}

# Plan-time note (static text shown in plan) and post-apply summary
output "plan_note" {
  value = "Plan note: creates network, SG, g6.xlarge worker using baked AMI if present (fallback to DLAMI), writes ssh_target.txt, can bake a golden AMI."
}

output "usage_summary" {
  value = <<-EON
          Next steps:
          1) SSH: ssh -i ~/.ssh/id_rsa ec2-user@${aws_instance.whisper_box.public_dns}
          2) Upload: scp -i ~/.ssh/id_rsa YOUR_FILE.mp3 ec2-user@${aws_instance.whisper_box.public_dns}:/home/ec2-user/
          3) Run: /home/ubuntu/venv/bin/insanely-fast-whisper --file-name YOUR_FILE.mp3 --model-name openai/whisper-large-v3 --batch-size 1 --flash true --timestamp chunk --transcript-path output.txt
          4) Bake AMI (when ready): tofu apply -target=aws_ami_from_instance.whisper_golden
          5) Override AMI (optional): set override_ami_id; otherwise uses latest baked or DLAMI
          6) ssh_target file: cat ${path.module}/ssh_target.txt
          EON
}
