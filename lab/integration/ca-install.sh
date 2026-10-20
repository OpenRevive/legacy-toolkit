#!/bin/bash
set -euo pipefail
test "$(hostname)" = openrevive-lab-sg
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends curl gpg ca-certificates openssl
install -d -m 755 /etc/apt/keyrings
curl -fsSL https://packages.smallstep.com/keys/apt/repo-signing-key.gpg -o /etc/apt/keyrings/smallstep.asc
cat > /etc/apt/sources.list.d/openrevive-smallstep.sources <<'EOF'
Types: deb
URIs: https://packages.smallstep.com/stable/debian
Suites: debs
Components: main
Signed-By: /etc/apt/keyrings/smallstep.asc
EOF
apt-get update -qq
apt-get install -y -qq step-cli step-ca
if ! id openrevive-ca >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/openrevive-ca --shell /usr/sbin/nologin openrevive-ca
fi
state=/var/lib/openrevive-ca
if [ -e "$state" ] && [ ! -f "$state/.openrevive-managed" ]; then
    echo 'Refusing an unowned CA directory' >&2; exit 1
fi
install -d -m 700 -o openrevive-ca -g openrevive-ca "$state"
touch "$state/.openrevive-managed"
if [ ! -e "$state/ca-password" ]; then
    python3 - "$state" <<'PY'
import os,secrets,sys
from pathlib import Path
p=Path(sys.argv[1])
for name in ['ca-password','provisioner-password']:
    fd=os.open(str(p/name),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(40)+'\n')
PY
    chown openrevive-ca:openrevive-ca "$state/ca-password" "$state/provisioner-password"
fi
if [ ! -f "$state/config/ca.json" ]; then
    runuser -u openrevive-ca -- env STEPPATH="$state" step ca init \
      --name 'OpenRevive Lab Issuing CA' --dns '127.0.0.1,ca.openrevive.test' \
      --address 127.0.0.1:9000 --provisioner openrevive-lab --deployment-type standalone \
      --password-file "$state/ca-password" --provisioner-password-file "$state/provisioner-password"
fi
if [ ! -f "$state/secrets/rsa_issuing_key.pem" ]; then
    umask 077
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -aes-256-cbc \
      -pass file:"$state/ca-password" -out "$state/secrets/rsa_issuing_key.pem" 2>/dev/null
    openssl req -new -sha256 -key "$state/secrets/rsa_issuing_key.pem" \
      -passin file:"$state/ca-password" -subj '/O=OpenRevive Lab/CN=OpenRevive Issuing CA 2026' \
      -out "$state/issuing.csr"
    chown openrevive-ca:openrevive-ca "$state/secrets/rsa_issuing_key.pem" "$state/issuing.csr"
fi
step version
step-ca version
printf 'OPENREVIVE_CA_CSR_READY\n'
