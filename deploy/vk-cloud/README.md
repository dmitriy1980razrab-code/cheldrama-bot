# VK Cloud deployment checkpoint

Domain: vash-kapeldiner.ru and www.vash-kapeldiner.ru.
Application: /opt/cheldrama-bot, service user botadmin without sudo.
Application listens on 127.0.0.1:8080.
Secrets, private keys and databases must not be committed.

## Configuration destinations

| Repository file | VM destination |
| --- | --- |
| 00-cheldrama-hardening.conf | /etc/ssh/sshd_config.d/00-cheldrama-hardening.conf |
| cheldrama-sshd.local | /etc/fail2ban/jail.d/cheldrama-sshd.local |
| security.conf | /etc/systemd/system/cheldrama-bot.service.d/security.conf |
| cheldrama-security.conf | /etc/nginx/conf.d/cheldrama-security.conf |
| cheldrama-bot | /etc/nginx/sites-available/cheldrama-bot |
| cheldrama-nginx | /etc/letsencrypt/renewal-hooks/deploy/cheldrama-nginx |

Base service: ../systemd/cheldrama-bot.service.
Nginx site is linked from /etc/nginx/sites-enabled/cheldrama-bot.
HTTPS configuration requires an issued certificate at the configured paths.
Renewal hook must be owned by root and executable (mode 750).

## Verified checkpoint

- Ubuntu 24.04 updated; SSH and UFW survived reboot.
- SSH uses public-key authentication; root login disabled.
- UFW allows TCP 22, 80 and 443; other incoming traffic denied.
- SSH source IP restriction is deferred by owner decision.
- Fail2ban: five failures in five minutes; initial ban one hour,
  increasing bans capped at one day. Manual ban/unban verified.
- Application commit 56d9f200cc5821a3b7909d1bc6b923a97a9a3336:
  221 tests passed on the VM.
- Theatre data synchronized; local API and browser chat verified.
- HTTPS certificate issued; renewal dry-run and deploy hook passed.
- Ten rapid requests with different session IDs: six HTTP 200,
  four HTTP 429.

Configuration backups are stored under /root on the VM.
Cloud VM backup is enabled; restoration has not been tested.

## Remaining work

- Configure VK/MAX secrets and verify channel delivery.
- Configure automatic theatre synchronization.
- Fix application rate limiter and bound website session storage.
- Remove plaintext storage of unrecognized VK/MAX requests.
- Define data retention, encrypted backups and key recovery.
- Harden persistent delivery failure handling.
- Implement administrator alerts and incident response.
- Build production admin authentication and permissions.
- Confirm provider DDoS coverage and audit dependency advisories.
- Run representative load and recovery checks.
