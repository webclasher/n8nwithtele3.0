#!/bin/bash
set -e

# ===============================
# Read required VARIABLES from environment (inline usage supported)
# Example usage:
# N8N_DOMAIN="jojo.n8nnode.com" \
# EMAIL="contact@n8nnode.com" \
# TELEGRAM_BOT_TOKEN="123:ABC" \
# AUTHORIZED_USER_ID="123456789" \
# N8N_USERNAME="admin" \
# N8N_PASSWORD="strongpassword" \
# curl -fsSL https://raw.githubusercontent.com/username/n8n-installer-complete/main/install.sh | sudo -E bash
# ===============================

N8N_DOMAIN=${N8N_DOMAIN:?Please set N8N_DOMAIN}
EMAIL=${EMAIL:?Please set EMAIL}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:?Please set TELEGRAM_BOT_TOKEN}
AUTHORIZED_USER_ID=${AUTHORIZED_USER_ID:?Please set AUTHORIZED_USER_ID}
N8N_USERNAME=${N8N_USERNAME:-admin}
N8N_PASSWORD=${N8N_PASSWORD:-password}

# Optional defaults
N8N_PORT=${N8N_PORT:-5678}
N8N_DATA=${N8N_DATA:-/root/n8n_data}
N8N_BACKUPS=${N8N_BACKUPS:-/root/n8n_backups}
N8N_LOGS=${N8N_LOGS:-/var/log/n8n}
BOT_FOLDER=${BOT_FOLDER:-/root/n8n_bot}

# GitHub raw URL for bot files (not used when running from zip)
BOT_RAW_URL="https://raw.githubusercontent.com/username/n8n-installer-complete/main/bot"

echo "Starting installation..."
export DEBIAN_FRONTEND=noninteractive

# Update & install packages
apt update && apt upgrade -y
apt install -y curl wget git python3 python3-pip python3-venv docker.io docker-compose nginx certbot fail2ban unzip

systemctl enable docker
systemctl start docker

# Create folders
mkdir -p "$N8N_DATA" "$N8N_BACKUPS" "$N8N_LOGS" "$BOT_FOLDER"
chmod -R 700 "$N8N_DATA" "$N8N_BACKUPS" "$N8N_LOGS" "$BOT_FOLDER"

# Deploy n8n container with basic auth enabled
docker pull n8nio/n8n:latest
docker rm -f n8n 2>/dev/null || true
docker run -d   --name n8n   -p ${N8N_PORT}:5678   -v ${N8N_DATA}:/home/node/.n8n   -v ${N8N_LOGS}:/var/log/n8n   -e N8N_HOST=${N8N_DOMAIN}   -e N8N_PORT=5678   -e N8N_BASIC_AUTH_ACTIVE=true   -e N8N_BASIC_AUTH_USER=${N8N_USERNAME}   -e N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}   -e NODE_ENV=production   --restart unless-stopped   n8nio/n8n:latest

# Setup Nginx reverse proxy
cat >/etc/nginx/sites-available/n8n <<'NGCONF'
server {
    listen 80;
    server_name REPLACE_DOMAIN;

    location / {
        proxy_pass http://localhost:5678;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGCONF

# replace domain placeholder
sed -i "s|REPLACE_DOMAIN|${N8N_DOMAIN}|g" /etc/nginx/sites-available/n8n
ln -sf /etc/nginx/sites-available/n8n /etc/nginx/sites-enabled/n8n
nginx -t && systemctl restart nginx

# Obtain SSL via certbot (nginx plugin)
certbot --nginx -d "${N8N_DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive || true

# Setup certbot renew cron
echo "0 3 * * * root certbot renew --post-hook 'systemctl reload nginx'" > /etc/cron.d/certbot-renew

# Fail2Ban
systemctl enable fail2ban
systemctl start fail2ban

# Setup Python virtualenv for bot
python3 -m venv "${BOT_FOLDER}/venv"
source "${BOT_FOLDER}/venv/bin/activate"
python3 -m pip install --upgrade pip

# If running from GitHub raw, uncomment following lines to download bot files
# curl -sSL ${BOT_RAW_URL}/requirements.txt -o ${BOT_FOLDER}/requirements.txt
# curl -sSL ${BOT_RAW_URL}/bot.py -o ${BOT_FOLDER}/bot.py

# When running from extracted repo, copy files into BOT_FOLDER manually before running.
# Install requirements
pip install -r "${BOT_FOLDER}/requirements.txt"

# Create .env for bot
cat > "${BOT_FOLDER}/.env" <<-EOD
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
AUTHORIZED_USER_ID=${AUTHORIZED_USER_ID}
N8N_API_URL=http://localhost:5678
EOD

# Create systemd service for bot
cat >/etc/systemd/system/n8n_bot.service <<'SERVICE'
[Unit]
Description=n8n Telegram Bot
After=network.target

[Service]
User=root
WorkingDirectory=REPLACE_BOT_FOLDER
EnvironmentFile=REPLACE_BOT_FOLDER/.env
ExecStart=REPLACE_BOT_FOLDER/venv/bin/python REPLACE_BOT_FOLDER/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sed -i "s|REPLACE_BOT_FOLDER|${BOT_FOLDER}|g" /etc/systemd/system/n8n_bot.service
systemctl daemon-reload
systemctl enable n8n_bot
systemctl start n8n_bot || true

echo "Installation finished. n8n should be available at https://${N8N_DOMAIN}"
echo "Bot service: systemctl status n8n_bot"
