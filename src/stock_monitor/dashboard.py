"""Small responsive, dependency-free simulation and trend-analysis dashboard."""

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .adapters import MockMarketDataAdapter
from .engine import AnalysisResult, StockMonitor
from .market_history import MarketHistoryError, get_long_term_analysis


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
body{font-family:system-ui;background:#07101d;color:#e8eef7;margin:auto;max-width:1100px;padding:12px}h1{margin:4px}.hero{text-align:center;padding:20px;border-radius:16px;background:#13233b}.score{font-size:54px;font-weight:800}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-top:10px}.card{background:#101c2d;border-radius:12px;padding:12px;margin-top:10px}.buy{color:#55e69c}.sell{color:#ff7185}table{width:100%;font-size:13px;border-collapse:collapse}td{text-align:right;padding:3px;border-bottom:1px solid #28374c}.bar{height:8px;background:#26364b}.fill{height:100%;background:#55e69c}small,.muted{color:#9dafc7}canvas{width:100%;height:170px}.trend-chart{height:250px}.trend-form{display:flex;gap:8px;flex-wrap:wrap}.trend-form input{flex:1;min-width:190px;background:#07101d;color:#e8eef7;border:1px solid #3b4e68;border-radius:10px;padding:12px;font-size:16px}.trend-form button{background:#55e69c;color:#07101d;border:0;border-radius:10px;padding:12px 18px;font-size:16px;font-weight:700;cursor:pointer}.trend-form button:disabled{opacity:.55;cursor:wait}.trend-head{display:flex;gap:10px;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;margin-top:12px}.trend-grade{font-size:32px;font-weight:800}.trend-label{font-size:18px;font-weight:700}.ma-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px;margin-top:10px}.ma{background:#13233b;border-radius:10px;padding:10px}.ma strong{font-size:18px}.up{color:#55e69c}.down{color:#ff7185}.neutral{color:#ffc857}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin:8px 0;color:#cbd8e8}.legend span:before{content:'●';margin-right:4px}.l-close:before{color:#fff}.l-25:before{color:#55e69c}.l-75:before{color:#ffc857}.l-125:before{color:#5ab8ff}.l-200:before{color:#df7cff}.error{color:#ff7185;padding-top:8px}@media(max-width:520px){.score{font-size:42px}.trend-grade{font-size:26px}}
</style>
<div class=card><h2>長期トレンド分析</h2><form class=trend-form id=trendForm><input id=trendQuery value='7203' placeholder='銘柄コード（例 7203）または会社名' aria-label='銘柄コードまたは会社名'><button id=trendButton type=submit>分析する</button></form><div id=trendStatus class=muted>25・75・125・200日移動平均線を計算します。</div><div id=trendResult hidden><div class=trend-head><div><div id=trendTitle class=trend-label></div><div id=trendMeta class=muted></div></div><div id=trendGrade class=trend-grade></div></div><div id=trendOrder></div><div id=maGrid class=ma-grid></div><div class=legend><span class=l-close>終値</span><span class=l-25>MA25</span><span class=l-75>MA75</span><span class=l-125>MA125</span><span class=l-200>MA200</span></div><canvas class=trend-chart id=trendChart></canvas><small id=trendSource></small></div></div>
<div class=hero><div id=state>LOADING</div><div class=score id=score>--</div><div id=quote></div></div><div class=card id=parts></div>
<div class=grid><div class=card><h3>板</h3><table id=book></table></div><div class=card><h3>歩み値</h3><table id=trades></table></div><div class=card><h3>注文フロー</h3><div id=flow></div></div><div class=card><h3>テクニカル</h3><div id=tech></div></div></div>
<div class=card><h3>Price / Pressure / Delta / OBI 時系列</h3><canvas id=chart></canvas></div><div class=card><h3>イベント</h3><div id=events></div><small id=notice></small></div>
<script>
const names={book:'板',trade_flow:'約定フロー',replenishment:'板補充',consumption:'板消化',cancellation:'取消',momentum:'短期Momentum',trend:'トレンド',macd:'MACD',volume:'出来高'};
function row(k,v){return `<div>${k}<b style=float:right>${typeof v==='number'?v.toFixed(2):v}</b></div>`}
function setupCanvas(c,height){const ratio=window.devicePixelRatio||1;c.width=Math.max(1,c.clientWidth*ratio);c.height=height*ratio;return {ctx:c.getContext('2d'),ratio};}
function draw(h){let c=document.getElementById('chart'),o=setupCanvas(c,170),x=o.ctx;x.clearRect(0,0,c.width,c.height);['price','raw','delta','obi'].forEach((k,j)=>{let a=h.map(z=>z[k]),mi=Math.min(...a),ma=Math.max(...a);x.strokeStyle=['#fff','#55e69c','#ffc857','#5ab8ff'][j];x.lineWidth=o.ratio;x.beginPath();a.forEach((v,i)=>{let px=i*c.width/Math.max(a.length-1,1),py=(.1+.8*(ma-v)/Math.max(ma-mi,1))*c.height;i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()})}
function drawTrend(h){const c=document.getElementById('trendChart'),o=setupCanvas(c,250),x=o.ctx;x.clearRect(0,0,c.width,c.height);const defs=[['close','#fff'],['ma25','#55e69c'],['ma75','#ffc857'],['ma125','#5ab8ff'],['ma200','#df7cff']];const all=[];for(const [k] of defs){for(const z of h){if(Number.isFinite(z[k]))all.push(z[k]);}}if(!all.length)return;let mi=Math.min(...all),ma=Math.max(...all),pad=Math.max((ma-mi)*.08,1);mi-=pad;ma+=pad;for(const [k,color] of defs){x.strokeStyle=color;x.lineWidth=(k==='close'?1.7:1.25)*o.ratio;x.beginPath();let started=false;h.forEach((z,i)=>{const v=z[k];if(!Number.isFinite(v)){started=false;return;}let px=i*c.width/Math.max(h.length-1,1),py=(ma-v)/(ma-mi)*c.height*.88+c.height*.06;if(!started){x.moveTo(px,py);started=true;}else{x.lineTo(px,py);}});x.stroke();}x.fillStyle='#9dafc7';x.font=`${11*o.ratio}px system-ui`;x.fillText(ma.toFixed(0),4*o.ratio,14*o.ratio);x.fillText(mi.toFixed(0),4*o.ratio,c.height-5*o.ratio);}
async function load(){try{let d=await(await fetch('/api')).json();document.getElementById('state').textContent=d.pressure.state.replaceAll('_',' ');document.getElementById('score').textContent=d.pressure.smoothed.toFixed(1)+' / 100';document.getElementById('quote').textContent=`${d.symbol} ${d.name}  ¥${d.price}  (${d.change>=0?'+':''}${d.change})`;document.getElementById('parts').innerHTML=Object.entries(d.pressure.components).map(([k,v])=>row(names[k],(v>=0?'+':'')+v)).join('');document.getElementById('book').innerHTML=[...d.asks.slice().reverse().map(x=>`<tr class=sell><td>ASK</td><td>${x.price}</td><td>${x.quantity}</td></tr>`),...d.bids.map(x=>`<tr class=buy><td>BID</td><td>${x.price}</td><td>${x.quantity}</td></tr>`)].join('');document.getElementById('trades').innerHTML=d.trades.map(x=>`<tr><td>${x.timestamp.slice(11,19)}</td><td>${x.price}</td><td>${x.quantity}</td><td>${x.aggressor}</td></tr>`).join('');document.getElementById('flow').innerHTML=['weighted_obi','trade_delta','bid_replenishment','ask_replenishment','bid_cancellation','ask_cancellation','bid_consumed_per_second','ask_consumed_per_second','bid_absorption','ask_absorption'].map(k=>row(k,d.flow[k])).join('');document.getElementById('tech').innerHTML=['ma5','ma25','ma75','trend','macd','macd_signal','macd_histogram','rsi14','bandwidth','volatility_expansion','volume_ratio'].map(k=>row(k,d.technical[k])).join('');document.getElementById('events').innerHTML=d.events.map(x=>'<div>'+x+'</div>').join('');document.getElementById('notice').textContent=d.notice;draw(d.history);}catch(e){document.getElementById('state').textContent='SIMULATION ERROR';}}
function arrow(direction){return direction==='上向き'?'↗':direction==='下向き'?'↘':'→'}
function trendClass(direction){return direction==='上向き'?'up':direction==='下向き'?'down':'neutral'}
async function loadTrend(query){const button=document.getElementById('trendButton'),status=document.getElementById('trendStatus'),result=document.getElementById('trendResult');button.disabled=true;status.className='muted';status.textContent='日足データを取得して計算中…';try{const response=await fetch('/api/trend?q='+encodeURIComponent(query));const d=await response.json();if(!response.ok||d.error)throw new Error(d.error||'取得に失敗しました');document.getElementById('trendTitle').textContent=`${d.symbol} ${d.name}`;document.getElementById('trendMeta').textContent=`${d.as_of} 終値 ¥${d.price.toLocaleString('ja-JP',{maximumFractionDigits:2})}`;document.getElementById('trendGrade').textContent=`${d.stars} ${d.label}`;document.getElementById('trendOrder').innerHTML=`<b>並び順:</b> ${d.order}`;document.getElementById('maGrid').innerHTML=Object.entries(d.moving_averages).map(([period,m])=>`<div class=ma><div>MA${period}</div><strong>¥${m.value.toLocaleString('ja-JP',{maximumFractionDigits:2})}</strong><div class=${trendClass(m.direction)}>${arrow(m.direction)} ${m.direction}（5日 ${m.slope_5d_pct>=0?'+':''}${m.slope_5d_pct.toFixed(2)}%）</div><small>株価との差 ${m.price_distance_pct>=0?'+':''}${m.price_distance_pct.toFixed(2)}%</small></div>`).join('');document.getElementById('trendSource').textContent=`データ元: ${d.source}。売買判断ではなくテクニカル状態の可視化です。`;result.hidden=false;status.textContent='';drawTrend(d.history);}catch(e){result.hidden=true;status.className='error';status.textContent=e.message;}finally{button.disabled=false;}}
document.getElementById('trendForm').addEventListener('submit',e=>{e.preventDefault();loadTrend(document.getElementById('trendQuery').value.trim());});
load();setInterval(load,2000);loadTrend(document.getElementById('trendQuery').value);
</script></html>"""


def serve(host: str = "0.0.0.0", port: int = 8000, symbol: str = "7203") -> None:
    """Run the built-in replay and expose a separate real daily-history endpoint."""
    results = list(StockMonitor(MockMarketDataAdapter()).run(symbol)); index = {"value": 0}; history: list[dict] = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            status = 200
            if parsed.path == "/api/trend":
                query = parse_qs(parsed.query).get("q", ["7203"])[0]
                try:
                    payload = get_long_term_analysis(query)
                except MarketHistoryError as exc:
                    status = 502
                    payload = {"error": str(exc)}
                except Exception:
                    status = 500
                    payload = {"error": "長期トレンド分析で予期しないエラーが発生しました"}
                body = json.dumps(payload, ensure_ascii=False, default=str).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            elif parsed.path == "/api":
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
