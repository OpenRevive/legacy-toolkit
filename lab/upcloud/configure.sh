#!/bin/bash
set -euo pipefail
test "$(hostname)" = openrevive-lab-sg
cd /root/openrevive-bootstrap

# Prevent packages from starting a default listener before the configuration and ACL exist.
created_policy=0
if [ ! -e /usr/sbin/policy-rc.d ]; then
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
    chmod 755 /usr/sbin/policy-rc.d
    created_policy=1
fi
cleanup_policy() {
    if [ "$created_policy" = 1 ]; then rm -f /usr/sbin/policy-rc.d; fi
}
trap cleanup_policy EXIT
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx dnsmasq bind9-dnsutils nftables ca-certificates curl

install -d -m 755 /etc/openrevive /etc/openrevive/certs /srv/openrevive-lab
install -m 600 leaf.key /etc/openrevive/certs/leaf.key
install -m 644 leaf.pem /etc/openrevive/certs/leaf.pem
install -m 644 index.html health.json root.cer /srv/openrevive-lab/
install -m 644 nginx.conf /etc/nginx/sites-available/openrevive-lab
ln -sfn /etc/nginx/sites-available/openrevive-lab /etc/nginx/sites-enabled/openrevive-lab
rm -f /etc/nginx/sites-enabled/default
install -m 644 dnsmasq.conf /etc/dnsmasq.d/openrevive-lab.conf
install -m 600 firewall.nft /etc/openrevive/firewall.nft
install -d -m 755 /usr/local/libexec
cat > /usr/local/libexec/openrevive-firewall <<'SH'
#!/bin/sh
set -eu
if /usr/sbin/nft list table inet openrevive_lab >/dev/null 2>&1; then
    /usr/sbin/nft delete table inet openrevive_lab
fi
exec /usr/sbin/nft -f /etc/openrevive/firewall.nft
SH
chmod 755 /usr/local/libexec/openrevive-firewall
cat > /etc/systemd/system/openrevive-firewall.service <<'UNIT'
[Unit]
Description=OpenRevive test server incoming access rules
After=network-pre.target
Before=nginx.service dnsmasq.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/libexec/openrevive-firewall

[Install]
WantedBy=multi-user.target
UNIT
nginx -t
dnsmasq --test
nft -c -f /etc/openrevive/firewall.nft
cleanup_policy
trap - EXIT
systemctl daemon-reload
systemctl enable openrevive-firewall.service nginx.service dnsmasq.service
systemctl restart openrevive-firewall.service
systemctl restart dnsmasq.service nginx.service
systemctl is-active --quiet nginx.service dnsmasq.service openrevive-firewall.service
# The second copy of the server key is no longer needed after installation.
rm -f /root/openrevive-bootstrap/leaf.key
printf 'UPCLOUD_SERVICES_ACTIVE\n'
