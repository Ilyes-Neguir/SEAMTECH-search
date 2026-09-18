# TLS Deployment & Reverse Proxy Guide

This document explains how to securely expose SEAMTECH Search on a local area network (LAN) or over the internet using a TLS-terminating reverse proxy.

---

## Security Model

SEAMTECH Search enforces a strict network security policy:
- **Localhost by default (`127.0.0.1` / `localhost`)**: Authentication tokens can safely traverse the local loopback interface.
- **Non-local interfaces (`0.0.0.0` or LAN IP)**: The backend **refuses** to serve token-authenticated HTTP requests on non-local interfaces unless `behind_tls_proxy=true` is explicitly configured.

```
[Browser / Client] --(HTTPS with TLS)--> [TLS Reverse Proxy (Caddy/Nginx)] --(HTTP / localhost)--> [SEAMTECH Backend]
```

### Key Configuration Settings

| Setting / Env Variable | Description | Default |
|------------------------|-------------|---------|
| `behind_tls_proxy` / `SEAMTECH_BEHIND_TLS_PROXY` | Informs backend that an upstream reverse proxy terminates TLS. | `false` in config; compose sets `true` for the web service (office deployment is behind a TLS terminator and web must bind `0.0.0.0`) and `false` for the frontend (controls the cookie `Secure` flag) |
| `allow_network_access` / `SEAMTECH_ALLOW_NETWORK_ACCESS` | Allows binding to non-local IP addresses. | `false` |
| `auth_token` / `SEAMTECH_AUTH_TOKEN` | Bearer token required when network access is enabled. | `null` |

---

## Reverse Proxy Options

### Option 1: Caddy (Recommended for LAN)

Caddy automatically manages internal TLS certificates with local CA trust.

#### Caddyfile Example

```caddy
seamtech.local {
    tls internal

    # Proxy frontend application
    handle /* {
        reverse_proxy 127.0.0.1:3000
    }

    # Proxy backend API directly (if bypassing Next.js proxy)
    handle /api/backend/* {
        uri strip_prefix /api/backend
        reverse_proxy 127.0.0.1:8000
    }
}
```

Run Caddy:
```bash
caddy run --config Caddyfile
```

---

### Option 2: Nginx with TLS Termination

```nginx
server {
    listen 443 ssl http2;
    server_name seamtech.voilerie.internal;

    ssl_certificate /etc/ssl/certs/seamtech.crt;
    ssl_certificate_key /etc/ssl/private/seamtech.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Forward headers
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Next.js Frontend
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

### Option 3: Cloudflare Tunnel

For secure remote access without exposing inbound firewall ports:

1. Install `cloudflared`.
2. Configure tunnel ingress:
   ```yaml
   tunnel: <TUNNEL_ID>
   credentials-file: /etc/cloudflared/<TUNNEL_ID>.json

   ingress:
     - hostname: search.your-domain.com
       service: http://127.0.0.1:3000
     - service: http_status:404
   ```
3. Run tunnel:
   ```bash
   cloudflared tunnel run
   ```

---

## Production Checklist

1. [ ] Set `SEAMTECH_BEHIND_TLS_PROXY=true` in environment or config.
2. [ ] Set strong `SEAMTECH_AUTH_TOKEN` (min 32 random characters).
3. [ ] Bind Docker published ports to `127.0.0.1` (configured in `docker-compose.yml`).
4. [ ] Verify probe endpoints (`/live` and `/ready`) return HTTP 200.
