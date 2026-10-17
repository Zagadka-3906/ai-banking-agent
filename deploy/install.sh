#!/usr/bin/env bash
# Install or update 安言 Core + Gateway on one Ubuntu 24.04 server (run as root).
#
#   bash install.sh <public-ip-or-domain> <path-to-extracted-source>
#
# Idempotent: secrets, TLS keys and data are created once and kept on re-runs;
# code, Python dependencies, systemd units and the Nginx site are refreshed.
# Both services listen on 127.0.0.1 only; Nginx is the single public entry (443).
set -euo pipefail

HOST="${1:?usage: install.sh <public-ip-or-domain> <source-dir>}"
SOURCE="${2:?usage: install.sh <public-ip-or-domain> <source-dir>}"
APP_USER=anyan
APP_DIR=/opt/anyan
DATA_DIR=/var/lib/anyan
CONF_DIR=/etc/anyan
PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$SOURCE/main.py" ] && [ -f "$SOURCE/agent_api.py" ] || { echo "no source in $SOURCE" >&2; exit 1; }

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-pip nginx openssl curl

echo "== swap (2 GiB RAM is tight while pip builds and the model client loads)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== user and directories"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o root -g root -m 755 "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$DATA_DIR" "$DATA_DIR/audit"
install -d -o root -g "$APP_USER" -m 750 "$CONF_DIR"
install -d -o root -g root -m 700 "$CONF_DIR/tls"

echo "== code"
rm -rf "$APP_DIR/app.new"
cp -a "$SOURCE" "$APP_DIR/app.new"
chown -R root:root "$APP_DIR/app.new"
rm -rf "$APP_DIR/app.old"
[ -d "$APP_DIR/app" ] && mv "$APP_DIR/app" "$APP_DIR/app.old"
mv "$APP_DIR/app.new" "$APP_DIR/app"

echo "== python environment"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -i "$PIP_INDEX" --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -i "$PIP_INDEX" -r "$APP_DIR/app/requirements-feishu.txt"

echo "== configuration (created once; edit $CONF_DIR/anyan.env afterwards)"
if [ ! -f "$CONF_DIR/anyan.env" ]; then
  sed -e "s|__AUTH_SECRET__|$(openssl rand -hex 32)|" \
      -e "s|__ADMIN_TOKEN__|$(openssl rand -hex 32)|" \
      "$APP_DIR/app/deploy/anyan.env.template" > "$CONF_DIR/anyan.env"
fi
chown root:"$APP_USER" "$CONF_DIR/anyan.env"
chmod 640 "$CONF_DIR/anyan.env"

echo "== TLS: a private CA trusted only by the 安言 app, and a certificate for $HOST"
if [ ! -f "$CONF_DIR/tls/ca.pem" ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
    -keyout "$CONF_DIR/tls/ca.key" -out "$CONF_DIR/tls/ca.pem" -subj "/CN=Anyan Demo CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
fi
if [ ! -f "$CONF_DIR/tls/server.pem" ] || ! openssl x509 -in "$CONF_DIR/tls/server.pem" -noout -checkhost "$HOST" -checkip "$HOST" 2>/dev/null | grep -q "does match"; then
  if [[ "$HOST" =~ ^[0-9.]+$ ]]; then SAN="IP:$HOST"; else SAN="DNS:$HOST"; fi
  openssl req -newkey rsa:2048 -nodes -keyout "$CONF_DIR/tls/server.key" -out "$CONF_DIR/tls/server.csr" -subj "/CN=$HOST" 2>/dev/null
  openssl x509 -req -in "$CONF_DIR/tls/server.csr" -CA "$CONF_DIR/tls/ca.pem" -CAkey "$CONF_DIR/tls/ca.key" -CAcreateserial \
    -days 825 -sha256 -out "$CONF_DIR/tls/server.pem" \
    -extfile <(printf "subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n" "$SAN") 2>/dev/null
  rm -f "$CONF_DIR/tls/server.csr"
fi
chmod 600 "$CONF_DIR"/tls/*.key

echo "== services"
install -m 644 "$APP_DIR/app/deploy/anyan-core.service" /etc/systemd/system/anyan-core.service
install -m 644 "$APP_DIR/app/deploy/anyan-gateway.service" /etc/systemd/system/anyan-gateway.service
systemctl daemon-reload
systemctl enable -q anyan-core anyan-gateway
systemctl restart anyan-core
sleep 3
systemctl restart anyan-gateway

echo "== nginx"
install -m 644 "$APP_DIR/app/deploy/nginx-anyan-stream.conf" /etc/nginx/anyan-stream.conf
sed "s|__SERVER_NAME__|$HOST|" "$APP_DIR/app/deploy/nginx-anyan.conf" > /etc/nginx/sites-available/anyan.conf
ln -sf /etc/nginx/sites-available/anyan.conf /etc/nginx/sites-enabled/anyan.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl enable -q nginx
systemctl reload nginx || systemctl restart nginx

echo "== cockpit panel (server-local only; reached through an SSH tunnel)"
# --no-install-recommends: the recommended set pulls in NetworkManager/PackageKit.
apt-get install -y -q --no-install-recommends cockpit-ws cockpit-system cockpit-bridge
install -d /etc/systemd/system/cockpit.socket.d
printf '[Socket]\nListenStream=\nListenStream=127.0.0.1:9090\n' > /etc/systemd/system/cockpit.socket.d/listen.conf
systemctl daemon-reload
systemctl enable -q --now cockpit.socket
systemctl restart cockpit.socket
# Root may not log in to Cockpit; "panel" (sudo) gets its password from `passwd panel`.
id panel >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo panel

echo "== health"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && curl -fsS http://127.0.0.1:8100/healthz >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null && echo "core ok"
curl -fsS http://127.0.0.1:8100/healthz >/dev/null && echo "gateway ok"
curl -fsS --cacert "$CONF_DIR/tls/ca.pem" --resolve "$HOST:443:127.0.0.1" "https://$HOST/gateway/healthz" >/dev/null && echo "https ok"
rm -rf "$APP_DIR/app.old"
