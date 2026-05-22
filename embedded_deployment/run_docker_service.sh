#!/bin/bash
# Stock Analysis Dashboard — Docker service manager
# For Yocto-based embedded systems without docker-compose.
# Requires: docker (daemon must be running)

set -e

IMAGE_NAME="stock-dashboard"
CONTAINER_NAME="stock-dashboard"
HOST_PORT="${HOST_PORT:-8502}"
CONTAINER_PORT="8000"
ETH_IFACE="${ETH_IFACE:-eth0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── helpers ────────────────────────────────────────────────────────────────────

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
err()  { echo "[ERROR] $*" >&2; }

# Resolve the IPv4 address of ETH_IFACE; fall back to 0.0.0.0 if not found.
eth_ip() {
    ip -4 addr show "${ETH_IFACE}" 2>/dev/null \
        | grep -oE 'inet [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' \
        | awk '{print $2}' \
        | head -1
}

# Build the host-side bind address: <eth0-ip>:<HOST_PORT>
bind_addr() {
    local ip
    ip="$(eth_ip)"
    if [ -z "${ip}" ]; then
        log "WARNING: ${ETH_IFACE} has no IPv4 address — binding to 0.0.0.0:${HOST_PORT}"
        echo "0.0.0.0:${HOST_PORT}"
    else
        echo "${ip}:${HOST_PORT}"
    fi
}

container_exists() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"
}

container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"
}

# ── commands ───────────────────────────────────────────────────────────────────

cmd_build() {
    log "Building image '${IMAGE_NAME}' from ${SCRIPT_DIR} ..."
    docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
    log "Build complete."
}

cmd_start() {
    cmd_build

    if container_exists; then
        log "Removing existing container '${CONTAINER_NAME}' ..."
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm   "${CONTAINER_NAME}" 2>/dev/null || true
    fi

    local ip
    ip="$(eth_ip)"
    log "Starting with --network host on ${ETH_IFACE} (${ip:-<no ip>}), port ${HOST_PORT} ..."

    # --network host: container shares the host network stack directly.
    # This avoids iptables/NAT (not available on all Yocto kernels).
    # We override the CMD port so HOST_PORT is honoured without port mapping.
    docker run -d \
        --name "${CONTAINER_NAME}" \
        --restart unless-stopped \
        --network host \
        "${IMAGE_NAME}" \
        python -m uvicorn api:app --host 0.0.0.0 --port "${HOST_PORT}"

    log "Service is up. Dashboard: http://${ip:-localhost}:${HOST_PORT}"
}

cmd_stop() {
    if container_running; then
        log "Stopping '${CONTAINER_NAME}' ..."
        docker stop "${CONTAINER_NAME}"
    else
        log "Container '${CONTAINER_NAME}' is not running."
    fi

    if container_exists; then
        docker rm "${CONTAINER_NAME}"
        log "Container removed."
    fi
}

cmd_restart() {
    cmd_stop
    cmd_start
}

cmd_status() {
    local ip
    ip="$(eth_ip)"
    echo "─── Network ────────────────────────────────────────────"
    echo "  Interface : ${ETH_IFACE}"
    echo "  IP        : ${ip:-<not found>}"
    echo "  Dashboard : http://${ip:-<eth0-ip>}:${HOST_PORT}"
    echo ""
    echo "─── Image ─────────────────────────────────────────────"
    docker images "${IMAGE_NAME}" --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}" 2>/dev/null || echo "(not built)"
    echo ""
    echo "─── Container ─────────────────────────────────────────"
    docker ps -a --filter "name=^${CONTAINER_NAME}$" \
        --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "(not found)"
}

cmd_logs() {
    if ! container_exists; then
        err "Container '${CONTAINER_NAME}' does not exist."
        exit 1
    fi
    log "Following logs for '${CONTAINER_NAME}' (Ctrl+C to stop) ..."
    docker logs -f "${CONTAINER_NAME}"
}

cmd_clean() {
    cmd_stop 2>/dev/null || true
    if docker image inspect "${IMAGE_NAME}" &>/dev/null; then
        log "Removing image '${IMAGE_NAME}' ..."
        docker rmi "${IMAGE_NAME}"
    fi
    log "Clean complete."
}

# ── usage ──────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  start     Build image (if needed) and start the container
  stop      Stop and remove the container
  restart   Stop then start
  build     Build the Docker image only
  status    Show image and container status
  logs      Follow container logs (Ctrl+C to exit)
  clean     Stop container and remove image

Environment variables:
  HOST_PORT   Host-side port to expose the dashboard (default: 18000)
  ETH_IFACE   Network interface to bind (default: eth0)

Examples:
  ./run_docker_service.sh start
  HOST_PORT=8080 ./run_docker_service.sh start
  ETH_IFACE=eth1 ./run_docker_service.sh start
  ./run_docker_service.sh logs
  ./run_docker_service.sh stop
EOF
}

# ── entrypoint ─────────────────────────────────────────────────────────────────

case "${1:-}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_restart ;;
    build)   cmd_build   ;;
    status)  cmd_status  ;;
    logs)    cmd_logs    ;;
    clean)   cmd_clean   ;;
    *)
        usage
        exit 1
        ;;
esac
