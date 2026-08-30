"""Start the iPhone-friendly long-term trend dashboard."""

import os

from . import irbank_history, market_history, trend_dashboard

# The existing dashboard imports market_history directly. Patch the provider
# entry points before starting the server so the UI uses IRBANK first while
# keeping the tested calculation and browser UI unchanged.
market_history.get_long_term_analysis = irbank_history.get_long_term_analysis
market_history.provider_status = irbank_history.provider_status
trend_dashboard.get_long_term_analysis = irbank_history.get_long_term_analysis
trend_dashboard.provider_status = irbank_history.provider_status
trend_dashboard.BUILD = "0.5.0"
trend_dashboard.HTML = trend_dashboard.HTML.replace("build 0.4.0", "build 0.5.0")


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("STOCK_MONITOR_PORT", "8000")))
    trend_dashboard.serve(
        os.getenv("STOCK_MONITOR_HOST", "0.0.0.0"),
        port,
    )
