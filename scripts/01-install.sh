#!/usr/bin/env bash
# Download, verify and install the AWS Emulator Debian package. Records sizes and version.
set -euo pipefail
OUT=/root/results/install.txt
BASE=https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS/latest
PKG=aws-emulator_amd64.deb
cd /root
: > "$OUT"

start=$(date +%s.%N)
curl -fsSLO "$BASE/$PKG"
curl -fsSLO "$BASE/$PKG.sig"
end=$(date +%s.%N)
echo "download_seconds: $(echo "$end - $start" | bc)" >> "$OUT"
echo "deb_bytes: $(stat -c %s $PKG)" >> "$OUT"

# Signature check, as described in the Linux getting-started guide
curl -fsSLo aws-publishing.asc https://downloads.aws.com/keys/gpg/current.asc
gpg --batch --import aws-publishing.asc 2>/dev/null || true
echo "key_fingerprint: $(gpg --fingerprint ssm-publishing@amazon.com | sed -n 2p | xargs)" >> "$OUT"
echo "signature: $(gpg --verify $PKG.sig $PKG 2>&1 | grep -o 'Good signature.*\|BAD signature.*')" >> "$OUT"

start=$(date +%s.%N)
apt-get install -y -q "./$PKG" > /root/results/install-apt.log 2>&1
end=$(date +%s.%N)
echo "apt_install_seconds: $(echo "$end - $start" | bc)" >> "$OUT"

echo "version: $(aws-emulator -v 2>&1 | head -1)" >> "$OUT"
echo "installed_bytes_opt: $(du -sb /opt/aws-emulator | cut -f1)" >> "$OUT"
echo "file_count_opt: $(find /opt/aws-emulator -type f | wc -l)" >> "$OUT"
echo "--- largest files" >> "$OUT"
find /opt/aws-emulator -type f -printf '%s %p\n' | sort -rn | head -5 >> "$OUT"
echo "--- binary" >> "$OUT"
file -L "$(command -v aws-emulator)" >> "$OUT"
ls -la "$(command -v aws-emulator)" >> "$OUT"
echo "--- ldd" >> "$OUT"
ldd "$(readlink -f "$(command -v aws-emulator)")" >> "$OUT" 2>&1 || true
aws-emulator --help > /root/results/aws-emulator-help.txt 2>&1 || true
cat "$OUT"
