#!/usr/bin/env bash
# One-time (idempotent) setup of the production VM. Run as root:
#
#   bash server_setup.sh <deploy_pubkey> <repo_url> [--harden]
#
# <deploy_pubkey> is the GitHub Actions deploy public key: a path to a .pub file or the key text.
# <repo_url> is the GitHub repo (https://github.com/owner/repo[.git] or git@github.com:owner/repo.git).
# --harden disables SSH password login and root password login. Only use it after proving key
# login works for both root and deploy from another terminal.
#
# Exit codes: 0 done, 1 error, 2 private repo: add the printed deploy key on GitHub, then rerun.
set -euo pipefail

HARDEN=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --harden) HARDEN=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
if [[ ${#ARGS[@]} -ne 2 ]]; then
  echo "usage: $0 <deploy_pubkey> <repo_url> [--harden]" >&2
  exit 1
fi
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

PUBKEY="${ARGS[0]}"
[[ -f "$PUBKEY" ]] && PUBKEY="$(cat "$PUBKEY")"
REPO_URL="${ARGS[1]}"
if [[ ! "$PUBKEY" =~ ^ssh-(ed25519|rsa)\  ]]; then
  echo "deploy_pubkey must be an ssh public key (ssh-ed25519 ...) or a path to one" >&2
  exit 1
fi

DEPLOY_USER=deploy
DEPLOY_HOME=/home/$DEPLOY_USER
APP_DIR=$DEPLOY_HOME/closure-call
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

as_deploy() { sudo -u "$DEPLOY_USER" -H "$@"; }
step() { echo; echo "==> $*"; }

# ---------------------------------------------------------------- packages
step "apt update/upgrade"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
apt-get -y -q install git curl ca-certificates

# ---------------------------------------------------------------- swap (frontend build can exceed 2 GB RAM)
step "swap"
if [[ -z "$(swapon --show --noheadings)" ]]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "added 2 GB /swapfile"
else
  echo "swap already present"
fi

# ---------------------------------------------------------------- docker
step "docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
docker compose version

# ---------------------------------------------------------------- deploy user
step "user $DEPLOY_USER"
id "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_HOME/.ssh"
AUTH="$DEPLOY_HOME/.ssh/authorized_keys"
touch "$AUTH"
grep -qxF "$PUBKEY" "$AUTH" || echo "$PUBKEY" >> "$AUTH"
chown "$DEPLOY_USER:$DEPLOY_USER" "$AUTH"
chmod 600 "$AUTH"

# ---------------------------------------------------------------- repo access
step "repository"
CLONE_URL="$REPO_URL"
if GIT_TERMINAL_PROMPT=0 git ls-remote "$REPO_URL" HEAD >/dev/null 2>&1; then
  echo "repo is publicly readable"
else
  # Private: read-only deploy key, cloned over SSH.
  SLUG="$(echo "$REPO_URL" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')"
  CLONE_URL="git@github.com:$SLUG.git"
  KEY="$DEPLOY_HOME/.ssh/github_deploy"
  [[ -f "$KEY" ]] || as_deploy ssh-keygen -q -t ed25519 -N "" -C "closure-call@$(hostname)" -f "$KEY"
  CFG="$DEPLOY_HOME/.ssh/config"
  if ! grep -q "^Host github.com" "$CFG" 2>/dev/null; then
    cat >> "$CFG" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
    chown "$DEPLOY_USER:$DEPLOY_USER" "$CFG"
    chmod 600 "$CFG"
  fi
  if ! as_deploy git ls-remote "$CLONE_URL" HEAD >/dev/null 2>&1; then
    echo
    echo "The repository is private. Add this PUBLIC key as a read-only deploy key:"
    echo "  GitHub > $SLUG > Settings > Deploy keys > Add deploy key (leave 'Allow write access' unchecked)"
    echo
    cat "$KEY.pub"
    echo
    echo "Then rerun this script."
    exit 2
  fi
  echo "deploy key accepted by GitHub"
fi

if [[ -d "$APP_DIR/.git" ]]; then
  as_deploy git -C "$APP_DIR" remote set-url origin "$CLONE_URL"
  as_deploy git -C "$APP_DIR" pull --ff-only origin main
else
  as_deploy git clone "$CLONE_URL" "$APP_DIR"
fi
chmod +x "$APP_DIR/scripts/"*.sh
as_deploy git -C "$APP_DIR" log -1 --format='checked out %h %s'

# ---------------------------------------------------------------- SSH hardening (opt-in)
if [[ $HARDEN -eq 1 ]]; then
  step "ssh hardening"
  for f in /root/.ssh/authorized_keys "$AUTH"; do
    [[ -s "$f" ]] || { echo "refusing to harden: $f is empty" >&2; exit 1; }
  done
  cat > /etc/ssh/sshd_config.d/00-closurecall.conf <<'EOF'
# Managed by closure-call scripts/server_setup.sh --harden. 00- so it wins over cloud-init's file
# (sshd uses the first value it reads for each option).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t
  systemctl restart ssh
  echo "password login disabled; root allowed with keys only"
fi

step "done"
[[ -f "$APP_DIR/.env" ]] || echo "next: copy .env to $APP_DIR/.env (chmod 600), then run scripts/deploy.sh as $DEPLOY_USER"
