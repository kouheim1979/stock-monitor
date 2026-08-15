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

現在値に近い上下 N ティック（既定 5）を解析対象にします。各価格に `quantity`, `previous_quantity`, `quantity_delta`, `executed_quantity`, `replenished_quantity`, `cancelled_quantity` を保持します。

スナップショットでは同一区間中の「新規注文」と「取消」を注文 ID 単位で分離できないため、保存則から**ネットの補充または取消**を推定します。

```text
net_queue_flow = current_quantity - previous_quantity + execution_quantity
replenishment = max(net_queue_flow, 0)
cancellation  = max(-net_queue_flow, 0)
TradeDelta = AggressiveBuyVolume - AggressiveSellVolume
NormalizedTradeFlow = TradeDelta / (BuyVolume + SellVolume)
OBI = (BidDepth - AskDepth) / (BidDepth + AskDepth)
```

この式により、前回表示数量より大きな約定が発生した場合も、その区間中に最低限必要だった補充量を取りこぼしません。Weighted OBI は近い順に既定 `[1.0, .8, .6, .4, .2]` を掛けます。売り約定量/秒を Bid consumption、買い約定量/秒を Ask consumption とします。Absorption は、**同じ価格レベルで約定があり、かつ閾値以上のネット補充が確認された場合**にのみ推定します。重み・深さ・閾値は `AnalysisConfig` で変更できます。

### テクニカル

日足から MA5/25/75 と 3 サンプル差の slope、EMA12−EMA26 の MACD、EMA9 signal/histogram、Wilder 平滑の RSI14（逆張り命令ではなく momentum 表示）、20MA ±1σ/±2σ と BandWidth、20 日平均に対する VolumeRatio を計算します。

MA は必要な期間が揃った場合だけその期間名で計算します。特に 75 日トレンドは、現在と 3 日前の 75 日窓を比較できる **78 点以上**の履歴がある場合だけ UPTREND/DOWNTREND を判定し、それ未満では NEUTRAL とします。Volatility expansion は 20 日窓を直前の 20 日窓と比較できる 40 点以上の履歴がある場合だけ判定します。

### Pressure Score v2

すべてを `[-1,+1]` に制限し、次式で説明可能な点数にします。

```text
raw = clamp(50 + 50 × Σ(weight_i × normalized_i) / Σweight, 0, 100)
smoothed[k] = 0.30 × raw[k] + 0.70 × smoothed[k-1]
```

既定重みは Weighted OBI 20%、Trade Flow 20%、Replenishment balance 12%、Consumption balance 12%、Cancellation balance 8%、短期価格 Momentum 8%、MA Trend 10%、MACD histogram 6%、方向付き Volume 4% です。出来高は方向そのものとはみなさず、**平均超の出来高が価格モメンタムの方向を補強する場合だけ**正負の材料として加えます。内訳は UI に符号付きで表示します。状態境界は `[20,40,60,80]`（強い売り / 売り / 中立 / 買い / 強い買い）で、重み・EMA・境界は `config.py` の dataclass を差し替え可能です。

## シミュレーションと画面

`simulation.json` は 1000 円中心の上下 5 本、998 円買い板 8000 株への 2000 株売り、板 7800 株（1800 株補充）、その後の売り板消化と価格上昇を再生します。画面は最上部の Pressure/状態/現在値/前日比、スコア内訳、板、歩み値、OBI、Delta、補充・取消・消化速度・Absorption、MA/MACD/RSI/Bollinger/出来高、および Price/Raw Pressure/Delta/OBI 時系列とイベントを表示します。

## セキュリティとリアルデータ TODO

- 秘密は `.env` または環境変数から読み、`.env` は Git 除外済みです。ログへ秘密を出しません。
- リアル接続には、利用する証券会社/情報サービス、公式 API 仕様、利用契約、認証方式、板・歩み値の配信権限が必要です。
- サービス決定後、公式仕様に沿った adapter、再接続、rate limit、時刻同期、欠落/訂正データ処理を追加します。
- 現在は一銘柄のオフライン replay、推定解析、表示、イベント生成までです。永続 DB、認証、リアル配信、発注は未実装です。
