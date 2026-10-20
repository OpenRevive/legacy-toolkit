#!/bin/bash
set -euo pipefail
test "$(hostname)" = openrevive-lab
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq --no-install-recommends nftables
install -d -m 755 /etc/openrevive-dns-ca-lab
cat > /etc/openrevive-dns-ca-lab/firewall.nft <<'NFT'
table inet openrevive_lab {
 chain input {
  type filter hook input priority 0; policy drop;
  ct state established,related accept
  iifname "lo" accept
  ct state invalid drop
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  ip saddr 192.168.0.0/24 tcp dport { 80, 443 } accept
 }
}
NFT
cat > /usr/local/sbin/openrevive-lab-firewall <<'SH'
#!/bin/sh
set -eu
if /usr/sbin/nft list table inet openrevive_lab >/dev/null 2>&1; then
 /usr/sbin/nft delete table inet openrevive_lab
fi
exec /usr/sbin/nft -f /etc/openrevive-dns-ca-lab/firewall.nft
SH
chmod 755 /usr/local/sbin/openrevive-lab-firewall
cat > /etc/systemd/system/openrevive-lab-firewall.service <<'UNIT'
[Unit]
Description=OpenRevive test CT LAN-only HTTP/HTTPS ingress
Before=nginx.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/openrevive-lab-firewall
[Install]
WantedBy=multi-user.target
UNIT
nft -c -f /etc/openrevive-dns-ca-lab/firewall.nft
systemctl daemon-reload
systemctl enable --now openrevive-lab-firewall
nft list table inet openrevive_lab
printf 'OPENREVIVE_CT_FIREWALL_ACTIVE\n'
