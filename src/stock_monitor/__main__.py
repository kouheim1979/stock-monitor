"""Start the iPhone-friendly long-term trend dashboard."""

import os

from . import jina_history, trend_dashboard

# The dashboard imports its provider functions at module import time, so point
# those entry points at the registration-free Jina -> Yahoo path before serving.
trend_dashboard.get_long_term_analysis = jina_history.get_long_term_analysis
trend_dashboard.provider_status = jina_history.provider_status
trend_dashboard.BUILD = "0.5.1"
trend_dashboard.HTML = trend_dashboard.HTML.replace("build 0.4.0", "build 0.5.1")


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("STOCK_MONITOR_PORT", "8000")))
    trend_dashboard.serve(
        os.getenv("STOCK_MONITOR_HOST", "0.0.0.0"),
        port,
    )
