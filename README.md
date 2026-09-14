# K-Stock Commander / stock-monitor

日本株を **「長期トレンド × 押し目 × モメンタム × 変動率」** で採点し、iPhoneで素早く確認する個人向けテクニカル判断ダッシュボードです。

> 発注・口座操作・自動売買はしません。表示するスコア・指値・崩れ確認ラインは、日足データから機械的に計算する参考値です。決算、ニュース、PER/PBR、配当、信用需給、呼値・単元株数は証券会社等で別途確認してください。

## v1.0 の主な機能

- 銘柄コード / 会社名検索
- MA25 / 75 / 125 / 200 の長期トレンド判定
- 0〜100 の **TECH SCORE** と A〜E ランク
- 「攻め / 本命 / 深押し」の3段階参考指値
- 20日・60日モメンタム、MA25乖離、年率換算ボラティリティ
- 60日高値からのドローダウン、上値見直し・崩れ確認ライン
- スコアを「なぜその点数か」まで内訳表示
- iPhone localStorage に予算・売買単位・ウォッチリストを保存
- ウォッチリスト一括採点とランキング
- 価格 / MAチャート
- Yahoo Finance 日足を Jina Reader 経由で取得し、12時間サーバーキャッシュ
- 取得不能時は既存の直接データ取得ルートへフォールバック

## ロジック

ベースの日足分析は 205営業日以上のデータから MA25 / 75 / 125 / 200 と5日傾きを計算します。`strategy.py` が次の要素を説明可能な形で合成します。

```text
TECH SCORE
  = 長期トレンド
  + MAの傾き
  + MAの並び順
  + MA25からの乖離（押し目 / 過熱）
  + 20日モメンタム
  - 高ボラティリティのペナルティ
```

強い下降 + 20日急落 + 短中期MA下向きの場合は falling-knife 判定を行い、スコア上限を抑えます。

参考指値は20日の日次変動率と MA25 / MA75 を使って3段階に配置します。TSEの実際の呼値単位は銘柄・価格帯等で異なるため、このアプリでは1円丸めの「参考値」として表示します。

## 起動

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
python -m stock_monitor
```

ブラウザで `http://localhost:8000` を開きます。

環境変数:

```text
STOCK_MONITOR_HOST=0.0.0.0
STOCK_MONITOR_PORT=8000
```

クラウド環境では `PORT` があれば優先します。

## API

```text
GET /api/analyze?q=8306
GET /api/trend?q=8306      # 互換エンドポイント
GET /health
```

`/api/analyze` は長期MA分析に `decision` を追加して返します。

## Render

既存の `render.yaml` をそのまま利用できます。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kouheim1979/stock-monitor)

## 構成

```text
src/stock_monitor/
  market_history.py   日足取得・MA分析
  jina_history.py     Jina Reader -> Yahoo Finance + cache
  strategy.py         TECH SCORE / 指値 / リスク判定
  pro_dashboard.py    iPhone向け UI + HTTP API
  orderflow.py        既存の板・約定解析エンジン
  indicators.py       既存テクニカル指標
```

旧 `trend_dashboard.py` と板・約定解析コードは残しているため、今後「長期判断」と「リアルタイム板圧力」を統合できます。

## テスト

```bash
pytest
```

`tests/test_strategy_decision.py` は、上昇ケースのスコア、下降ケースの低スコア、3段階指値の順序を検証します。

## 次の拡張候補

- 決算・PER/PBR・配当利回り・ROEを使ったファンダメンタルスコア
- 日経平均 / TOPIX / ドル円を使った地合いフィルター
- RSI / MACD / 出来高を長期判断画面へ統合
- 証券会社の**公式API仕様に沿った**リアルタイム株価アダプタ
- 通知（指値接近、スコア急変、決算予定）
- 認証・永続DB・複数端末同期

## 注意

このソフトウェアは個人向けの分析・可視化用途です。投資助言、利益保証、売買執行を行うものではありません。外部データ元の仕様変更や利用制限により取得できない場合があります。
