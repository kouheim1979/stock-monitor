from stock_monitor import BookSnapshot, OrderFlowAnalyzer, Trade, calculate_pressure_score


previous = BookSnapshot(
    bids={999.0: 2000, 998.0: 8000, 997.0: 12000},
    asks={1001.0: 1000, 1002.0: 3000, 1003.0: 5000},
)

# Example: 2,000 shares are sold into the 998 bid, but the displayed bid only
# falls from 8,000 to 7,800. Queue conservation therefore estimates that
# 1,800 shares were replenished at 998 during the observation window.
current = BookSnapshot(
    bids={999.0: 2000, 998.0: 7800, 997.0: 12000},
    asks={1001.0: 400, 1002.0: 3000, 1003.0: 5000},
)

trades = [
    Trade(price=998.0, quantity=2000, side="sell"),
    Trade(price=1001.0, quantity=600, side="buy"),
]

result = OrderFlowAnalyzer(depth_levels=3).analyze(previous, current, trades)
score = calculate_pressure_score(result)

print(f"pressure score: {score.score}/100 ({score.state})")
print(f"book imbalance: {score.book_imbalance:+.3f}")
print(f"trade flow: {score.trade_flow:+.3f}")
print(f"replenishment bias: {score.replenishment_bias:+.3f}")

for change in result.bid_changes:
    if change.replenished_quantity or change.cancelled_quantity:
        print(
            "BID",
            change.price,
            f"replenished={change.replenished_quantity}",
            f"cancelled={change.cancelled_quantity}",
        )
