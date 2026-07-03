"""Console entry point for the Tympany web server (``poetry run tympany-serve``).

Configuration via environment:
    HOST             bind address (default 127.0.0.1; use 0.0.0.0 behind a proxy)
    PORT             bind port    (default 8000)
    TYMPANY_DEV=1    enable --reload for local development (off by default)
    WEB_CONCURRENCY  worker processes (default 1; multi-worker needs SECRET_KEY set)
"""

import os

import uvicorn


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    if os.environ.get("TYMPANY_DEV", "").strip() == "1":
        uvicorn.run("web.app:app", host=host, port=port, reload=True)
    else:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
        uvicorn.run("web.app:app", host=host, port=port, workers=workers)


if __name__ == "__main__":
    main()
