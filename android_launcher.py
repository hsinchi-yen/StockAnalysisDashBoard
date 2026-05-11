from __future__ import annotations

import os


def start_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    cache_dir: str | None = None,
    static_dir: str | None = None,
    finmind_timeout: float = 60.0,
) -> None:
    if cache_dir:
        os.environ["MICROECO_CACHE_DIR"] = cache_dir
    if static_dir:
        os.environ["MICROECO_STATIC_DIR"] = static_dir
    # Android networks can be slow; use a longer FinMind API timeout by default.
    os.environ.setdefault("MICROECO_FINMIND_TIMEOUT", str(finmind_timeout))

    import uvicorn

    uvicorn.run(
        "api:app",
        host=host,
        port=int(port),
        reload=False,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    start_server(
        host=os.environ.get("MICROECO_HOST", "127.0.0.1"),
        port=int(os.environ.get("MICROECO_PORT", "8000")),
        cache_dir=os.environ.get("MICROECO_CACHE_DIR"),
        static_dir=os.environ.get("MICROECO_STATIC_DIR"),
    )