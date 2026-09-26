"""Start Laya with a one-shot warm-up before opening its HTTP port.

Laya's upstream ``laya-serve`` preloads weights but does not run a forward pass.
On CUDA, the first forward can therefore exceed Alpha's short System One request
budget even though later calls are fast. This wrapper uses the same upstream
``build_router``/``create_app`` functions, warms the loaded router once, and only
then starts Uvicorn. It is executed with the isolated Laya interpreter, not the
main Alpha environment.
"""

from __future__ import annotations

import logging
import os

import uvicorn
from laya.serve import build_router, create_app

logger = logging.getLogger("system_one_laya_server")


def main() -> None:
    logging.basicConfig(level=os.getenv("LAYA_LOG_LEVEL", "info").upper())
    router = build_router()
    warmup_state = "Laya server warm-up"
    warmup_questions = {
        "ready": {
            "type": "noul",
            "instructions": "Is the decision service ready?",
        }
    }
    try:
        router.predict(warmup_state, warmup_questions)
        logger.info("Laya warm-up completed; opening the System One endpoint")
    except Exception:
        # Keep the upstream service available if an optional warm-up question is
        # rejected by a future checkpoint. The first real request will still
        # expose the error, and the Gateway's fallback contract remains intact.
        logger.exception("Laya warm-up failed; starting the server anyway")

    app = create_app(router=router)
    uvicorn.run(
        app,
        # Keep unauthenticated local defaults loopback-only. Operators can
        # explicitly set LAYA_HOST=0.0.0.0 when LAYA_API_KEY is configured on
        # both the server and Alpha.
        host=os.getenv("LAYA_HOST", "127.0.0.1"),
        port=int(os.getenv("LAYA_PORT", "8000")),
        log_level=os.getenv("LAYA_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
