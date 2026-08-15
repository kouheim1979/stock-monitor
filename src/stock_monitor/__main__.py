"""Start the no-credentials simulation dashboard."""

import os
from .dashboard import serve

if __name__ == "__main__":
    serve(os.getenv("STOCK_MONITOR_HOST", "0.0.0.0"), int(os.getenv("STOCK_MONITOR_PORT", "8000")),
          os.getenv("STOCK_MONITOR_SYMBOL", "7203"))
