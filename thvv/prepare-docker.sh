#!/usr/bin/env bash
# Called only by explicit deployment preparation. Never removes existing engines/data.
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo '仅支持 Linux 自动准备 Docker'; exit 1; }
[[ "$EUID" == 0 ]] || { echo '请使用 sudo bash thvv/prepare-docker.sh'; exit 1; }
[[ -d /run/systemd/system ]] || { echo '需要由使用 systemd 的宿主机准备 Docker'; exit 1; }

if command -v docker >/dev/null && docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
    echo 'Docker 已可用，复用已有安装。'
    exit 0
fi

if ! command -v dockerd >/dev/null || ! command -v docker >/dev/null; then
    source /etc/os-release
    case "$ID" in
        ubuntu|debian)
            codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
            [[ "$codename" =~ ^[a-z0-9]+$ ]] || { echo '无法确定发行版代号，停止自动安装'; exit 1; }
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            apt-get install -y --no-remove ca-certificates curl
            # Reuse a configured official repository; do not overwrite administrator files.
            if ! grep -Rqs "download.docker.com/linux/$ID" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
                [[ ! -e /etc/apt/sources.list.d/thvv-docker.sources ]] || { echo '现有 THVV Docker 源配置需要手动核对'; exit 1; }
                install -m 0755 -d /etc/apt/keyrings
                curl --proto '=https' --tlsv1.2 -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/thvv-docker.asc
                chmod 0644 /etc/apt/keyrings/thvv-docker.asc
                arch="$(dpkg --print-architecture)"
                cat > /etc/apt/sources.list.d/thvv-docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $codename
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/thvv-docker.asc
EOF
            fi
            apt-get update
            apt-get install -y --no-remove docker-ce docker-ce-cli containerd.io
            ;;
        centos|rocky|almalinux|ol|rhel|fedora)
            command -v dnf >/dev/null || { echo '需要可用的 dnf 包管理器'; exit 1; }
            distro=centos
            [[ "$ID" == rhel ]] && distro=rhel
            [[ "$ID" == fedora ]] && distro=fedora
            dnf install -y ca-certificates curl
            if ! grep -Rqs "download.docker.com/linux/$distro" /etc/yum.repos.d; then
                [[ ! -e /etc/yum.repos.d/thvv-docker-ce.repo ]] || { echo '现有 THVV Docker 源配置需要手动核对'; exit 1; }
                curl --proto '=https' --tlsv1.2 -fsSL "https://download.docker.com/linux/$distro/docker-ce.repo" -o /etc/yum.repos.d/thvv-docker-ce.repo
            fi
            dnf install -y docker-ce docker-ce-cli containerd.io
            ;;
        *) echo "当前发行版 $ID 尚不支持自动安装，请先按 Docker 官方文档安装"; exit 1 ;;
    esac
fi

systemctl start docker
docker info --format '{{.ServerVersion}}'
echo 'Docker 安装和启动完成。请使用运行网页的账号重新检查访问权限。'
