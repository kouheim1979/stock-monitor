"""Small responsive, dependency-free simulation web dashboard."""

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .adapters import MockMarketDataAdapter
from .engine import AnalysisResult, StockMonitor


def _payload(result: AnalysisResult, history: list[dict]) -> dict:
    snap = result.frame.snapshot
    return {"symbol": snap.symbol, "name": snap.name, "price": snap.last_price,
            "change": snap.last_price-snap.previous_close, "pressure": asdict(result.pressure),
            "flow": asdict(result.flow), "technical": asdict(result.technical),
            "bids": [asdict(x) for x in snap.bids], "asks": [asdict(x) for x in snap.asks],
            "trades": [{**asdict(x), "timestamp": x.timestamp.isoformat()} for x in result.frame.trades],
            "events": result.events, "history": history,
            "notice": "補充・取消・Absorption はスナップショット差分による推定値です。"}


HTML = """<!doctype html><html lang=ja><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Stock Monitor</title><style>
body{font-family:system-ui;background:#07101d;color:#e8eef7;margin:auto;max-width:1100px;padding:12px}h1{margin:4px}.hero{text-align:center;padding:20px;border-radius:16px;background:#13233b}.score{font-size:54px;font-weight:800}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-top:10px}.card{background:#101c2d;border-radius:12px;padding:12px}.buy{color:#55e69c}.sell{color:#ff7185}table{width:100%;font-size:13px;border-collapse:collapse}td{text-align:right;padding:3px;border-bottom:1px solid #28374c}.bar{height:8px;background:#26364b}.fill{height:100%;background:#55e69c}small{color:#9dafc7}canvas{width:100%;height:170px}</style>
<div class=hero><div id=state>LOADING</div><div class=score id=score>--</div><div id=quote></div></div><div class=card id=parts></div>
<div class=grid><div class=card><h3>板</h3><table id=book></table></div><div class=card><h3>歩み値</h3><table id=trades></table></div><div class=card><h3>注文フロー</h3><div id=flow></div></div><div class=card><h3>テクニカル</h3><div id=tech></div></div></div>
<div class=card><h3>Price / Pressure / Delta / OBI 時系列</h3><canvas id=chart></canvas></div><div class=card><h3>イベント</h3><div id=events></div><small id=notice></small></div>
<script>const names={book:'板',trade_flow:'約定フロー',replenishment:'板補充',consumption:'板消化',cancellation:'取消',momentum:'短期Momentum',trend:'トレンド',macd:'MACD',volume:'出来高'};
function row(k,v){return `<div>${k}<b style=float:right>${typeof v==='number'?v.toFixed(2):v}</b></div>`}function draw(h){let c=document.querySelector('canvas'),x=c.getContext('2d');c.width=c.clientWidth*devicePixelRatio;c.height=170*devicePixelRatio;x.clearRect(0,0,c.width,c.height);['price','raw','delta','obi'].forEach((k,j)=>{let a=h.map(z=>z[k]),mi=Math.min(...a),ma=Math.max(...a);x.strokeStyle=['#fff','#55e69c','#ffc857','#5ab8ff'][j];x.beginPath();a.forEach((v,i)=>{let px=i*c.width/Math.max(a.length-1,1),py=(.1+.8*(ma-v)/Math.max(ma-mi,1))*c.height;i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()})}
async function load(){let d=await(await fetch('/api')).json();state.textContent=d.pressure.state.replaceAll('_',' ');score.textContent=d.pressure.smoothed.toFixed(1)+' / 100';quote.textContent=`${d.symbol} ${d.name}  ¥${d.price}  (${d.change>=0?'+':''}${d.change})`;parts.innerHTML=Object.entries(d.pressure.components).map(([k,v])=>row(names[k],(v>=0?'+':'')+v)).join('');book.innerHTML=[...d.asks.slice().reverse().map(x=>`<tr class=sell><td>ASK</td><td>${x.price}</td><td>${x.quantity}</td></tr>`),...d.bids.map(x=>`<tr class=buy><td>BID</td><td>${x.price}</td><td>${x.quantity}</td></tr>`)].join('');trades.innerHTML=d.trades.map(x=>`<tr><td>${x.timestamp.slice(11,19)}</td><td>${x.price}</td><td>${x.quantity}</td><td>${x.aggressor}</td></tr>`).join('');flow.innerHTML=['weighted_obi','trade_delta','bid_replenishment','ask_replenishment','bid_cancellation','ask_cancellation','bid_consumed_per_second','ask_consumed_per_second','bid_absorption','ask_absorption'].map(k=>row(k,d.flow[k])).join('');tech.innerHTML=['ma5','ma25','ma75','trend','macd','macd_signal','macd_histogram','rsi14','bandwidth','volatility_expansion','volume_ratio'].map(k=>row(k,d.technical[k])).join('');events.innerHTML=d.events.map(x=>'<div>'+x+'</div>').join('');notice.textContent=d.notice;draw(d.history)}load();setInterval(load,2000)</script></html>"""


def serve(host: str = "0.0.0.0", port: int = 8000, symbol: str = "7203") -> None:
    """Run the built-in replay once, exposing successive results on each poll."""
    results = list(StockMonitor(MockMarketDataAdapter()).run(symbol)); index = {"value": 0}; history: list[dict] = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api"):
                result = results[index["value"]]; index["value"] = (index["value"]+1) % len(results)
                if index["value"] == 1: history.clear()
                history.append({"price": result.frame.snapshot.last_price, "raw": result.pressure.raw,
                                "smoothed": result.pressure.smoothed, "delta": result.flow.trade_delta, "obi": result.flow.weighted_obi})
                body = json.dumps(_payload(result, history), ensure_ascii=False, default=str).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                body = HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, format, *args): return
    print(f"Stock Monitor: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
