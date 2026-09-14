from stock_monitor.strategy import build_strategy


def sample(grade=5, slope=1.0, price=1200.0):
    history=[]
    p=800.0
    for i in range(260):
        p *= 1.0012
        history.append({"date": f"d{i}", "close": p})
    price = price or p
    return {
        "price": price,
        "analysis_price": price,
        "grade": grade,
        "label": "trend",
        "order": "株価 > MA25 > MA75 > MA125 > MA200",
        "moving_averages": {
            "25": {"value": price*0.98, "slope_5d_pct": slope},
            "75": {"value": price*0.94, "slope_5d_pct": slope},
            "125": {"value": price*0.90, "slope_5d_pct": slope},
            "200": {"value": price*0.84, "slope_5d_pct": slope},
        },
        "history": history[:-1] + [{"date":"latest","close":price}],
    }


def test_strategy_has_order_ladder():
    result=build_strategy(sample())
    ladder=result["limit_prices"]
    assert ladder["aggressive"] > ladder["standard"] > ladder["deep"] > 0
    assert 0 <= result["score"] <= 100


def test_falling_market_scores_worse():
    strong=build_strategy(sample())
    weak=sample(grade=1, slope=-2.0, price=700.0)
    weak["moving_averages"]={
        "25":{"value":760,"slope_5d_pct":-2.0},
        "75":{"value":820,"slope_5d_pct":-1.5},
        "125":{"value":900,"slope_5d_pct":-1.0},
        "200":{"value":980,"slope_5d_pct":-0.8},
    }
    weak["history"]=[{"date":f"d{i}","close":1000-i*1.2} for i in range(250)] + [{"date":"latest","close":700}]
    result=build_strategy(weak)
    assert result["score"] < strong["score"]
    assert result["rank"] in {"D","E"}
