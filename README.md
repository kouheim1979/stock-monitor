# stock-monitor

日本株の板・歩み値・日足を、**観測・解析・表示だけ**行う個人向けリアルタイム・モニターです。発注、口座操作、自動売買は実装していません。証券 API がなくても同梱 JSON リプレイで全機能を確認できます。

> **重要:** 板の補充・取消・Absorption は、板スナップショットと約定の差分から得る**推定値**です。取引所の注文 ID を復元するものではなく、真の注文理由を保証しません。UI にもこの注意を常時表示します。

## クイックスタート

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
python -m stock_monitor
```

Safari で `http://localhost:8000` を開きます。同一 Wi-Fi の iPhone からは、PC の LAN IP を調べて `http://<PCのIP>:8000` を開いてください（OS のファイアウォールで TCP 8000 を LAN 内だけ許可）。公開インターネットへ直接露出させないでください。`STOCK_MONITOR_HOST`、`STOCK_MONITOR_PORT`、`STOCK_MONITOR_SYMBOL` は環境変数で変更できます。

## アーキテクチャ

```text
MarketDataAdapter (Mock / Replay / 将来の公式API)
  ↓
Book Level State (previous, delta, execution, replenishment, cancellation)
  ↓
Trade classification (Aggressive Buy / Sell)
  ↓
OrderFlowAnalyzer (OBI, weighted OBI, absorption, consume rate)
  ↓
Technical Indicators (SMA/EMA, MA, MACD, RSI, Bollinger, volume)
  ↓
Signal normalization / EMA smoothing
  ↓
PressureScore
  ↓
Dependency-free responsive Dashboard
```

取得・解析・UI は分離されています。`MarketDataAdapter` は `get_snapshot`、`get_recent_trades`、`stream` を定義します。`ReplayMarketDataAdapter` は JSON、`MockMarketDataAdapter` は同梱シナリオを使います。将来の証券会社実装は、**公式仕様を確認してから**このポートに追加します。現時点では仕様・認証情報が指定されていないため、推測したリアル API 実装は置いていません。

Streamlit は導入が速い一方、依存が大きく更新制御も限定的です。FastAPI + HTML/JS は API 拡張性が高い一方、この単一利用シミュレーターには ASGI 依存が増えます。そこで初版は標準ライブラリ HTTP server + responsive HTML/JS を採用しました。ゼロ依存で軽量、iPhone でも表示でき、解析エンジンと完全分離されています。本番公開時には認証・TLS・堅牢な ASGI サーバーを追加してください。

## 解析仕様

### 板・約定

上下 N ティック（既定 5、最大は入力次第）を扱います。各価格に `quantity`, `previous_quantity`, `quantity_delta`, `executed_quantity`, `replenished_quantity`, `cancelled_quantity` を保持します。

```text
expected_after_execution = max(previous_quantity - execution_quantity, 0)
replenishment = max(current_quantity - expected_after_execution, 0)
cancellation  = max(expected_after_execution - current_quantity, 0)
TradeDelta = AggressiveBuyVolume - AggressiveSellVolume
NormalizedTradeFlow = TradeDelta / (BuyVolume + SellVolume)
OBI = (BidDepth - AskDepth) / (BidDepth + AskDepth)
```

Weighted OBI は近い順に既定 `[1.0, .8, .6, .4, .2]` を掛けます。売り約定量/秒を Bid consumption、買い約定量/秒を Ask consumption とします。約定を受けながら閾値以上再補充された板を Absorption と推定します。重み・深さ・閾値は `AnalysisConfig` で変更できます。

### テクニカル

日足から MA5/25/75 と 3 サンプル差の slope、EMA12−EMA26 の MACD、EMA9 signal/histogram、RSI14（逆張り命令ではなく momentum 表示）、20MA ±1σ/±2σ と BandWidth、20 日平均に対する VolumeRatio を計算します。価格と MA の順序および全 slope が揃ったときだけ UPTREND/DOWNTREND です。直前区間より BandWidth が 25% 超拡大すると volatility expansion とします。

### Pressure Score v2

すべてを `[-1,+1]` に制限し、次式で説明可能な点数にします。

```text
raw = clamp(50 + 50 × Σ(weight_i × normalized_i) / Σweight, 0, 100)
smoothed[k] = 0.30 × raw[k] + 0.70 × smoothed[k-1]
```

既定重みは Weighted OBI 20%、Trade Flow 20%、Replenishment balance 12%、Consumption balance 12%、Cancellation balance 8%、短期価格 Momentum 8%、MA Trend 10%、MACD histogram 6%、方向付き Volume 4% です。内訳は UI に符号付きで表示します。状態境界は `[20,40,60,80]`（強い売り / 売り / 中立 / 買い / 強い買い）で、重み・EMA・境界は `config.py` の dataclass を差し替え可能です。

## シミュレーションと画面

`simulation.json` は 1000 円中心の上下 5 本、998 円買い板 8000 株への 2000 株売り、板 7800 株（1800 株補充）、その後の売り板消化と価格上昇を再生します。画面は最上部の Pressure/状態/現在値/前日比、スコア内訳、板、歩み値、OBI、Delta、補充・取消・消化速度・Absorption、MA/MACD/RSI/Bollinger/出来高、および Price/Raw Pressure/Delta/OBI 時系列とイベントを表示します。

## セキュリティとリアルデータ TODO

- 秘密は `.env` または環境変数から読み、`.env` は Git 除外済みです。ログへ秘密を出しません。
- リアル接続には、利用する証券会社/情報サービス、公式 API 仕様、利用契約、認証方式、板・歩み値の配信権限が必要です。
- サービス決定後、公式仕様に沿った adapter、再接続、rate limit、時刻同期、欠落/訂正データ処理を追加します。
- 現在は一銘柄のオフライン replay、推定解析、表示、イベント生成までです。永続 DB、認証、リアル配信、発注は未実装です。
