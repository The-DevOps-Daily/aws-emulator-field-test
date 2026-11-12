#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 x86_64 host: AWS CLI v2, Python + boto3, Node + AWS SDK v3,
# Terraform, Docker. Run as root on a throwaway machine.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get install -y -q unzip jq python3-venv python3-pip docker.io gnupg curl time sysstat bc

# AWS CLI v2 (the emulator docs ask for 2.35.0 or later for the agent skill)
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
rm -rf /tmp/aws && unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --update

# Terraform from the HashiCorp apt repo
curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --batch --yes --dearmor -o /usr/share/keyrings/hashicorp.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/hashicorp.list
apt-get update -q && apt-get install -y -q terraform

# Node.js 22 for the JavaScript SDK v3 test
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y -q nodejs

# Python venv with boto3 (and moto for the comparison run)
python3 -m venv /opt/venv
/opt/venv/bin/pip install -q --upgrade pip
/opt/venv/bin/pip install -q boto3 'moto[server]'

systemctl enable --now docker

{
  echo "aws: $(aws --version 2>&1)"
  echo "terraform: $(terraform version | head -1)"
  echo "node: $(node --version)"
  echo "boto3: $(/opt/venv/bin/python -c 'import boto3; print(boto3.__version__)')"
  echo "moto: $(/opt/venv/bin/python -c 'import moto; print(moto.__version__)')"
  echo "docker: $(docker --version)"
  echo "kernel: $(uname -r)"
  echo "os: $(lsb_release -ds)"
  echo "cpus: $(nproc)"
  echo "mem_mib: $(free -m | awk '/Mem:/{print $2}')"
} | tee /root/results/host-versions.txt
