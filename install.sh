#!/bin/bash
set -e

# ===============================
# REQUIRED VARIABLES (pass inline)
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
N8N_LOGS=${N8N_LOGS:-/var/log/n8n}
N8N_BACKUPS=${N8N_BACKUPS:-/root/n8n_backups}
BOT_FOLDER=${BOT_FOLDER:-/root/n8n_bot}

# GitHub raw URLs
BOT_PY_URL="https://raw.githubusercontent.com/webclasher/n8nwithtele3.0/main/bot.py"
REQUIREMENTS_URL="https://raw.githubusercontent.com/webclasher/n8nwithtele3.0/main/requirements.txt"

# ===============================
# DNS & Ports Pre-Check
# ===============================
echo "🔎 Checking DNS and port availability..."
SERVER_IP=$(curl -s ifconfig.me)
DNS_IP=$(dig +short $N8N_DOMAIN | tail -n1)

if [[ "$DNS_IP" != "$SERVER_IP" ]]; then
    echo "⚠ DNS for $N8N_DOMAIN ($DNS_IP) does not point to this VPS ($SERVER_IP)."
    echo "Please update your DNS and try again."
    exit 1
fi

for port in 80 443 $N8N_PORT; do
    if ss -tuln | grep -q ":$port "; then
        echo "⚠ Port $port is already in use. Please free the port and rerun the script."
        exit 1
    fi
done
echo "✅ DNS and ports check passed."

# ===============================
# Update system & install packages
# ===============================
apt update && apt upgrade -y
apt install -y curl wget git python3 python3-pip python3-venv docker.io docker-compose nginx certbot fail2ban unzip dnsutils

systemctl enable docker
systemctl start docker

# ===============================
# Create folders
# ===============================
mkdir -p "$N8N_DATA" "$N8N_LOGS" "$N8N_BACKUPS" "$BOT_FOLDER"
chmod -R 700 "$N8N_DATA" "$N8N_LOGS" "$N8N_BACKUPS" "$BOT_FOLDER"

# ===============================
# Deploy n8n container
# ===============================
docker pull n8nio/n8n:latest
docker rm -f n8n 2>/dev/null || true
docker run -d \
  --name n8n \
  -p ${N8N_PORT}:5678 \
  -v ${N8N_DATA}:/home/node/.n8n \
  -v ${N8N_LOGS}:/var/log/n8n \
  -e N8N_HOST=${N8N_DOMAIN} \
  -e N8N_PORT=5678 \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=${N8N_USERNAME} \
  -e N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD} \
  -e NODE_ENV=production \
  --restart unless-stopped \
  n8nio/n8n:latest

# ===============================
# Nginx reverse proxy + SSL
# ===============================
cat >/etc/nginx/sites-available/n8n <<NGCONF
server {
    listen 80;
    server_name ${N8N_DOMAIN};

    location / {
        proxy_pass http://localhost:${N8N_PORT};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGCONF

ln -sf /etc/nginx/sites-available/n8n /etc/nginx/sites-enabled/n8n
nginx -t
systemctl restart nginx
certbot --nginx -d "${N8N_DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive || true
echo "0 3 * * * root certbot renew --post-hook 'systemctl reload nginx'" > /etc/cron.d/certbot-renew

# ===============================
# Fail2Ban
# ===============================
systemctl enable fail2ban
systemctl start fail2ban

# ===============================
# Setup Python Telegram Bot
# ===============================
python3 -m venv "$BOT_FOLDER/venv"
source "$BOT_FOLDER/venv/bin/activate"
pip install --upgrade pip
curl -sSL "$BOT_PY_URL" -o "$BOT_FOLDER/bot.py"
curl -sSL "$REQUIREMENTS_URL" -o "$BOT_FOLDER/requirements.txt"
pip install -r "$BOT_FOLDER/requirements.txt"

# Create .env
cat >"$BOT_FOLDER/.env" <<EOL
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
AUTHORIZED_USER_ID=${AUTHORIZED_USER_ID}
N8N_API_URL=http://localhost:${N8N_PORT}
N8N_DATA=${N8N_DATA}
N8N_BACKUPS=${N8N_BACKUPS}
EOL

# ===============================
# Create systemd service for bot
# ===============================
cat >/etc/systemd/system/n8n_bot.service <<EOL
[Unit]
Description=n8n Telegram Bot
After=network.target

[Service]
User=root
WorkingDirectory=${BOT_FOLDER}
EnvironmentFile=${BOT_FOLDER}/.env
ExecStart=${BOT_FOLDER}/venv/bin/python ${BOT_FOLDER}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

systemctl daemon-reload
systemctl enable n8n_bot
systemctl start n8n_bot || true

echo "✅ Installation complete!"
echo "n8n: https://${N8N_DOMAIN}"
echo "Bot service: systemctl status n8n_bot"
