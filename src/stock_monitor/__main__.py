"""Start the iPhone-friendly long-term trend dashboard."""

import os

from .trend_dashboard import serve


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("STOCK_MONITOR_PORT", "8000")))
    serve(
        os.getenv("STOCK_MONITOR_HOST", "0.0.0.0"),
        port,
    )
