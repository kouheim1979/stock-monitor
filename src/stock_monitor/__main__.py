"""Start K-Stock Commander."""

import os

from . import pro_dashboard


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("STOCK_MONITOR_PORT", "8000")))
    pro_dashboard.serve(
        os.getenv("STOCK_MONITOR_HOST", "0.0.0.0"),
        port,
    )
