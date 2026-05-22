# Stock Analysis Dashboard — Embedded Deployment Guide

Target platform: Yocto-based embedded Linux (Docker available, no docker-compose)
Deploy path: `/home/STOCKANALYSISDASHBOARD/`
Dashboard port: **18000** (configurable via `HOST_PORT`)

---

## Prerequisites

On the embedded system, verify Docker is available and the daemon is running:

```sh
docker info
```

If the daemon is not running, start it according to your Yocto init system (systemd or SysVinit):

```sh
# systemd
systemctl start docker

# SysVinit / manual
/etc/init.d/docker start
# or
dockerd &
```

---

## Step 1 — Copy files to the embedded system

From your development machine (Windows/Linux), run **one** of the following:

```sh
# scp
scp -r ./embedded_deployment root@10.88.90.96:/home/STOCKANALYSISDASHBOARD

# rsync (preferred — skips unchanged files on re-deploy)
rsync -avz --delete ./embedded_deployment/ root@10.88.90.96:/home/STOCKANALYSISDASHBOARD/
```

---

## Step 2 — Set permissions on the run script

On the embedded system:

```sh
ssh root@10.88.90.96
cd /home/STOCKANALYSISDASHBOARD
chmod +x run_docker_service.sh
```

---

## Step 3 — Build and start the service

```sh
./run_docker_service.sh start
```

This command:
1. Builds the `stock-dashboard` Docker image from the local `Dockerfile`.
2. Removes any previously running container with the same name.
3. Starts a new container with `--restart unless-stopped` so it survives reboots as long as the Docker daemon itself starts on boot.

Expected output:

```
[2026-05-13 12:00:00] Building image 'stock-dashboard' ...
...
[2026-05-13 12:03:00] Build complete.
[2026-05-13 12:03:00] Starting container 'stock-dashboard' on host port 18000 ...
[2026-05-13 12:03:01] Service is up. Dashboard: http://10.88.90.96:18000
```

---

## Step 4 — Verify the service

```sh
./run_docker_service.sh status
./run_docker_service.sh logs     # Ctrl+C to stop following
```

Open a browser and navigate to:

```
http://10.88.90.96:18000
```

---

## Changing the host port

Set the `HOST_PORT` environment variable before calling the script:

```sh
HOST_PORT=8080 ./run_docker_service.sh start
```

---

## Service management reference

| Command | Description |
|---|---|
| `./run_docker_service.sh start` | Build image + start container |
| `./run_docker_service.sh stop` | Stop and remove container |
| `./run_docker_service.sh restart` | Stop then start |
| `./run_docker_service.sh build` | Build image only (no container) |
| `./run_docker_service.sh status` | Show image and container info |
| `./run_docker_service.sh logs` | Follow live container logs |
| `./run_docker_service.sh clean` | Remove container and image |

---

## Auto-start on boot (optional)

If your Yocto image uses **systemd**, create a unit file:

```sh
cat > /etc/systemd/system/stock-dashboard.service <<'EOF'
[Unit]
Description=Stock Analysis Dashboard
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/STOCKANALYSISDASHBOARD
ExecStart=/home/STOCKANALYSISDASHBOARD/run_docker_service.sh start
ExecStop=/home/STOCKANALYSISDASHBOARD/run_docker_service.sh stop

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stock-dashboard
systemctl start stock-dashboard
```

If your system uses **SysVinit**, add the following to `/etc/rc.local` before the `exit 0` line:

```sh
/home/STOCKANALYSISDASHBOARD/run_docker_service.sh start &
```

---

## Re-deploying an update

From your development machine:

```sh
rsync -avz --delete ./embedded_deployment/ root@10.88.90.96:/home/STOCKANALYSISDASHBOARD/
ssh root@10.88.90.96 "cd /home/STOCKANALYSISDASHBOARD && ./run_docker_service.sh restart"
```

The `restart` command rebuilds the image from the updated source files before launching the new container.

---

## Troubleshooting

**Container fails to start — port already in use**

```sh
# Find what is using the port
netstat -tlnp | grep 18000
# Kill it or use a different HOST_PORT
HOST_PORT=18001 ./run_docker_service.sh start
```

**Build fails — no internet on embedded system**

The `Dockerfile` installs Python packages from PyPI. If the embedded system has no outbound internet access, pre-pull the image on a connected machine, save it, and load it:

```sh
# On development machine
docker build -t stock-dashboard ./embedded_deployment
docker save stock-dashboard | gzip > stock-dashboard.tar.gz
scp stock-dashboard.tar.gz root@10.88.90.96:/home/STOCKANALYSISDASHBOARD/

# On embedded system — load and run without building
docker load < /home/STOCKANALYSISDASHBOARD/stock-dashboard.tar.gz
docker run -d \
  --name stock-dashboard \
  --restart unless-stopped \
  -p 18000:8000 \
  stock-dashboard
```

**View application errors**

```sh
./run_docker_service.sh logs
```

**Check disk space (image is ~500 MB)**

```sh
df -h
docker system df
```

---

## File inventory

```
/home/STOCKANALYSISDASHBOARD/
├── Dockerfile                  # Container build definition
├── .dockerignore               # Files excluded from Docker context
├── requirements.txt            # Python dependencies
├── api.py                      # FastAPI application entry point
├── cache.py                    # Local JSON cache layer
├── charts.py                   # Chart-building helpers
├── datasource_finmind.py       # FinMind API client
├── datasource_goodinfo.py      # Goodinfo scraper client
├── datasource_mops.py          # MOPS data fetcher
├── series_builder.py           # Financial series utilities
├── static/
│   ├── index.html              # Dashboard single-page app
│   ├── app.js                  # Frontend JavaScript
│   └── styles.css              # Styles
├── run_docker_service.sh       # Service manager (this project's docker-compose replacement)
└── guideline.md                # This file
```
