# Remote Yocto Deploy — Operating Rules

> Version: 2026-11-01  
> Target: `root@10.1.1.230` — Yocto aarch64 (pico-imx8mm), Docker only (no docker-compose)  
> Applies to: StockAnalysisDashBoard (FastAPI + uvicorn, ARM64)

---

## Before you deploy: the host is shared

`root@10.1.1.230` also runs twse, macro, memdash (with PostgreSQL), twhold and twstock
containers, and its root filesystem holds both Docker data and the OS. As of 2026-11-01
it is 92% full with about 200 MB of memory available.

**Prefer the [incremental update](#incremental-update-recommended).** The full image
flow below writes ~680 MB of new layers (plus a 700 MB tar) and can fill the disk under
the other services. Use it only when the base image itself must change, and check
`df -h /` first. Do not run `docker image prune`: it removes other projects' dangling
images too. Remove only images you confirmed are this project's orphans.

---

## Incremental update (recommended)

Layers the new code onto the running image via `embedded_deployment/Dockerfile.update`:
a few MB of disk, the same dependency versions, and the old image stays as `:prev`.

```bash
# 1. Commit first, then ship exactly that commit
git archive HEAD:embedded_deployment | ssh root@10.1.1.230 \
    'rm -rf /root/STOCKANALYSISDASHBOARD/update && mkdir -p /root/STOCKANALYSISDASHBOARD/update \
     && tar -x -C /root/STOCKANALYSISDASHBOARD/update'

# 2. Keep the running image for rollback, then build on top of it.
#    --network host: this kernel has no NAT, so pip can't reach PyPI from a bridge network.
ssh root@10.1.1.230 'docker tag stock-dashboard:arm64 stock-dashboard:prev \
    && cd /root/STOCKANALYSISDASHBOARD/update \
    && docker build --network host -f Dockerfile.update -t stock-dashboard:new .'

# 3. Swap the container (about 10 seconds of downtime)
ssh root@10.1.1.230 'docker tag stock-dashboard:new stock-dashboard:arm64 \
    && docker stop stock-dashboard && docker rm stock-dashboard && \
    docker run -d --name stock-dashboard --restart unless-stopped --network host \
    stock-dashboard:arm64 python -m uvicorn api:app --host 0.0.0.0 --port 8502'

# 4. After verifying, remove the build leftovers
ssh root@10.1.1.230 'rm -rf /root/STOCKANALYSISDASHBOARD/update; docker rmi stock-dashboard:new'
```

Add `-e FINMIND_API_KEY=...` to `docker run` to enable the 02:00 precompute. Without it
the nightly job only cleans the cache. The cache lives in the container, so a deploy
starts it cold.

Then verify (Step 5). If it fails, use [Rollback](#rollback). The `:prev` tag already
exists, so skip the tagging step there. After a later successful deploy, the previous
`:prev` has no tag and can be removed once `docker ps -a` shows nothing using it.

---

## Prerequisites (local machine)

| Requirement | Check |
|---|---|
| Docker Desktop with buildx | `docker buildx version` |
| `arm64-builder` buildx builder | `docker buildx inspect arm64-builder` |
| SSH key or password for `root@10.1.1.230` | `ssh root@10.1.1.230 echo ok` |
| `embedded_deployment/Dockerfile` | ARM64-compatible, no x86 assumptions |
| `embedded_deployment/run_docker_service.sh` | Service manager script |

Create the arm64 builder once:
```powershell
docker buildx create --name arm64-builder --use
docker buildx inspect arm64-builder --bootstrap
```

---

## Step 1 — Code is committed

Never build from a dirty working tree. Verify before building:
```powershell
git status --short    # should be empty or only untracked docs
git log --oneline -3
```

---

## Step 2 — Cross-compile the image (local)

```powershell
docker buildx build --builder arm64-builder --platform linux/arm64 --load `
    --tag stock-dashboard:arm64 `
    --file embedded_deployment\Dockerfile `
    embedded_deployment
docker save stock-dashboard:arm64 -o stock-dashboard-arm64.tar
```

---

## Step 3 — Transfer to remote

```powershell
$sshOpts = @("-o","StrictHostKeyChecking=no","-o","ConnectTimeout=10")
scp @sshOpts stock-dashboard-arm64.tar embedded_deployment\run_docker_service.sh `
    root@10.1.1.230:/root/STOCKANALYSISDASHBOARD/
```

---

## Step 4 — Deploy on remote

> **Note:** `run_docker_service.sh start` always rebuilds the image from local files.
> To deploy the **pre-built arm64 image** from the tar, use `docker run` directly instead.

```bash
ssh root@10.1.1.230
cd /root/STOCKANALYSISDASHBOARD

# Load pre-built image
docker load -i stock-dashboard-arm64.tar

# Stop & remove old container
docker stop stock-dashboard 2>/dev/null || true
docker rm   stock-dashboard 2>/dev/null || true

# Run container from the loaded arm64 image (no local rebuild)
docker run -d \
  --name stock-dashboard \
  --restart unless-stopped \
  --network host \
  stock-dashboard:arm64 \
  python -m uvicorn api:app --host 0.0.0.0 --port 8502

# Remove the tar after load
rm -f stock-dashboard-arm64.tar
```

---

## Step 5 — Verify container health

```bash
ssh root@10.1.1.230 "docker inspect --format 'Status: {{.State.Status}}' stock-dashboard"
ssh root@10.1.1.230 "curl -sf http://localhost:8502/api/health && echo OK"
```

Expected: `Status: running` and `OK`.

Browser access:
```
http://10.1.1.230:8502
```

---

## Step 6 — Cleanup

```bash
# Confirm tar is gone (run_docker_service.sh does NOT remove it automatically)
ssh root@10.1.1.230 "rm -f /root/STOCKANALYSISDASHBOARD/stock-dashboard-arm64.tar && echo removed"

# Check images in use before removing anything else
ssh root@10.1.1.230 "docker ps -a --format '{{.Image}}'"
ssh root@10.1.1.230 "docker rmi <image:tag>"   # only confirmed orphans
```

---

## Rollback

The old image is retained as `<none>:<none>` only until `docker image prune` runs.
Tag the previous image **before** deploying if rollback must be possible:

```bash
ssh root@10.1.1.230 "docker tag stock-dashboard:arm64 stock-dashboard:prev"
# deploy new image
# if rollback needed:
ssh root@10.1.1.230 "docker stop stock-dashboard && docker rm stock-dashboard && \
    HOST_PORT=8502 docker run -d --name stock-dashboard --restart unless-stopped \
    --network host stock-dashboard:prev \
    python -m uvicorn api:app --host 0.0.0.0 --port 8502"
```

---

## Key paths on remote

| Path | Purpose |
|---|---|
| `/root/STOCKANALYSISDASHBOARD/` | Deployment directory |
| `/root/STOCKANALYSISDASHBOARD/run_docker_service.sh` | Service manager (updated on each deploy) |
| Container `/app/` | Application code (ephemeral — lost on image change) |

---

## Port mapping

| Layer | Port |
|---|---|
| Container internal (uvicorn) | `8000` |
| Host-side (`HOST_PORT`) | `8502` |
| External access | `http://10.1.1.230:8502` |

Because `--network host` is used, there is no Docker NAT. The container process binds directly to `0.0.0.0:8502` on the host.

---

## Useful runtime commands

```bash
# Follow logs
ssh root@10.1.1.230 "docker logs -f stock-dashboard"

# Container status
ssh root@10.1.1.230 "cd /root/STOCKANALYSISDASHBOARD && HOST_PORT=8502 ./run_docker_service.sh status"

# Restart without re-deploying
ssh root@10.1.1.230 "cd /root/STOCKANALYSISDASHBOARD && HOST_PORT=8502 ./run_docker_service.sh restart"
```
