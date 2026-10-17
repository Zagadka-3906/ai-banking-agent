#!/usr/bin/env bash
# Deploy 安言 from this checkout (Git Bash / Linux / macOS).
#
#   deploy/deploy.sh server <host>   upload the committed HEAD and install/update it
#   deploy/deploy.sh keys   <host>   copy DEEPSEEK_API_KEY / DASHSCOPE_API_KEY from ./.env
#   deploy/deploy.sh app    <host>   build an APK that talks to https://<host>/
#   deploy/deploy.sh status <host>   service state and health
#   deploy/deploy.sh panel  <host>   open 安言运维台 at http://localhost:9100 through SSH (Ctrl+C closes)
#   deploy/deploy.sh cockpit <host>  open Cockpit at https://localhost:9090 through SSH (Ctrl+C closes)
#
# SSH uses ~/.ssh/id_anyan_deploy as root (override with ANYAN_SSH_KEY / ANYAN_SSH_USER).
# Only committed files are uploaded; .env, logs and build output never leave this machine,
# except the two model keys when "keys" is run explicitly. Keys never appear in arguments.
set -euo pipefail

ACTION="${1:?usage: deploy.sh server|keys|app|status|panel|cockpit <host>}"
HOST="${2:?usage: deploy.sh server|keys|app|status|panel|cockpit <host>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/deploy"
KEY="${ANYAN_SSH_KEY:-$HOME/.ssh/id_anyan_deploy}"
TARGET="${ANYAN_SSH_USER:-root}@$HOST"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TARGET")
mkdir -p "$OUT"

case "$ACTION" in
  server)
    [ -z "$(git -C "$ROOT" status --porcelain -- . ':!build')" ] || echo "note: uncommitted changes are not deployed" >&2
    echo "== uploading $(git -C "$ROOT" rev-parse --short HEAD)"
    git -C "$ROOT" archive --format=tar HEAD | "${SSH[@]}" 'rm -rf /root/anyan-src && mkdir -p /root/anyan-src && tar -x -C /root/anyan-src'
    "${SSH[@]}" "bash /root/anyan-src/deploy/install.sh '$HOST' /root/anyan-src"
    "${SSH[@]}" 'cat /etc/anyan/tls/ca.pem' > "$OUT/anyan-ca.pem"
    echo "CA saved to build/deploy/anyan-ca.pem (needed by: deploy.sh app $HOST)"
    ;;
  keys)
    [ -f "$ROOT/.env" ] || { echo "no .env in $ROOT" >&2; exit 1; }
    grep -E '^(DEEPSEEK_API_KEY|DASHSCOPE_API_KEY)=.+' "$ROOT/.env" | tr -d '\r' | "${SSH[@]}" "python3 -c '
import re, sys
keys = dict(line.rstrip(\"\\n\").split(\"=\", 1) for line in sys.stdin if \"=\" in line)
path = \"/etc/anyan/anyan.env\"
text = open(path, encoding=\"utf-8\").read()
for name, value in keys.items():
    text = re.sub(r\"(?m)^\" + name + r\"=.*$\", lambda m: name + \"=\" + value, text)
open(path, \"w\", encoding=\"utf-8\").write(text)
print(\"updated:\", \", \".join(sorted(keys)) or \"nothing\")
' && systemctl restart anyan-gateway"
    ;;
  app)
    [ -f "$OUT/anyan-ca.pem" ] || "${SSH[@]}" 'cat /etc/anyan/tls/ca.pem' > "$OUT/anyan-ca.pem"
    (cd "$ROOT/android" && ./gradlew${WINDIR:+.bat} --console=plain -q :app:assembleDebug \
      -PbankCoreUrl="https://$HOST/core/" -PbankGatewayUrl="https://$HOST/gateway/" -PbankServerCa="$OUT/anyan-ca.pem")
    cp "$ROOT/android/app/build/outputs/apk/debug/app-debug.apk" "$OUT/anyan-$HOST.apk"
    echo "APK: build/deploy/anyan-$HOST.apk"
    ;;
  status)
    "${SSH[@]}" 'systemctl --no-pager --lines=0 status anyan-core anyan-gateway nginx | grep -E "●|Active:"; curl -fsS http://127.0.0.1:8000/healthz; echo; curl -fsS http://127.0.0.1:8100/healthz; echo'
    ;;
  panel)
    # The back office listens on the server's 127.0.0.1 only; this tunnel is the way in.
    echo "安言运维台: open http://localhost:9100 and paste this access token:"
    "${SSH[@]}" 'cat /etc/anyan/panel.token'
    echo "Ctrl+C closes the tunnel."
    ssh -i "$KEY" -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -N -L 9100:127.0.0.1:9100 "$TARGET"
    ;;
  cockpit)
    # Cockpit listens on the server's 127.0.0.1 only; this tunnel is the way in.
    echo "Cockpit: open https://localhost:9090 (user: panel). Ctrl+C closes the tunnel."
    ssh -i "$KEY" -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -N -L 9090:127.0.0.1:9090 "$TARGET"
    ;;
  *) echo "unknown action: $ACTION" >&2; exit 1 ;;
esac
