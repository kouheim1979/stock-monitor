"""Simple iPhone-friendly long-term moving-average dashboard."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .market_history import MarketHistoryError, get_long_term_analysis

BUILD = "0.3.2"

HTML = r'''<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Stock Trend Monitor</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#07101d;color:#e8eef7;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:900px;margin:auto;padding:16px}.card{background:#101c2d;border-radius:16px;padding:16px;margin-bottom:14px}h1{font-size:27px;margin:0 0 6px}h2{font-size:20px;margin:0 0 12px}.sub,.muted{color:#9dafc7}.form{display:flex;gap:10px;flex-wrap:wrap}.form input{flex:1;min-width:190px;background:#07101d;color:#fff;border:1px solid #41536d;border-radius:12px;padding:14px;font-size:18px}.form button{border:0;border-radius:12px;padding:14px 20px;background:#55e69c;color:#07101d;font-size:17px;font-weight:800}.form button:disabled{opacity:.55}.error{color:#ff7185;margin-top:10px;white-space:pre-wrap}.ok{color:#55e69c}.headline{font-size:24px;font-weight:800;margin-top:12px}.grade{font-size:25px;font-weight:800;margin:8px 0}.order{font-size:15px;overflow-wrap:anywhere}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}.ma{background:#13233b;border-radius:12px;padding:12px}.ma .v{font-size:21px;font-weight:800}.up{color:#55e69c}.down{color:#ff7185}.flat{color:#ffc857}canvas{display:block;width:100%;height:260px;background:#0b1626;border-radius:12px;margin-top:12px}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#bdc9d8;margin-top:8px}.dot:before{content:"●";margin-right:4px}.c0:before{color:#fff}.c25:before{color:#55e69c}.c75:before{color:#ffc857}.c125:before{color:#5ab8ff}.c200:before{color:#df7cff}.build{font-size:11px;color:#73849a;text-align:right}@media(min-width:700px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
</style>
<div class="wrap">
  <div class="card">
    <h1>長期トレンド分析</h1>
    <div class="sub">25・75・125・200日移動平均線</div>
    <form id="f" class="form" style="margin-top:14px">
      <input id="q" value="8301" inputmode="search" autocomplete="off" placeholder="8301 または 三菱UFJ">
      <button id="b" type="submit">分析する</button>
    </form>
    <div id="status" class="muted" style="margin-top:10px">銘柄コードまたは会社名を入力してください。</div>
    <div class="build">build 0.3.2</div>
  </div>
  <div id="result" class="card" hidden>
    <div id="title" class="headline"></div>
    <div id="meta" class="muted"></div>
    <div id="grade" class="grade"></div>
    <div id="order" class="order"></div>
    <div id="grid" class="grid"></div>
    <div class="legend"><span class="dot c0">終値</span><span class="dot c25">MA25</span><span class="dot c75">MA75</span><span class="dot c125">MA125</span><span class="dot c200">MA200</span></div>
    <canvas id="chart"></canvas>
    <div id="source" class="muted" style="margin-top:8px;font-size:12px"></div>
  </div>
</div>
<script>
(function(){
'use strict';
var f=document.getElementById('f'),q=document.getElementById('q'),b=document.getElementById('b'),status=document.getElementById('status'),result=document.getElementById('result');
function cls(d){return d==='上向き'?'up':d==='下向き'?'down':'flat'}
function arrow(d){return d==='上向き'?'↗':d==='下向き'?'↘':'→'}
function fmt(n){var x=Number(n);return isFinite(x)?x.toLocaleString('ja-JP',{maximumFractionDigits:2}):'-'}
function draw(rows){
  var c=document.getElementById('chart'),ratio=window.devicePixelRatio||1,w=Math.max(280,c.clientWidth),h=260;c.width=Math.floor(w*ratio);c.height=Math.floor(h*ratio);var x=c.getContext('2d');x.setTransform(ratio,0,0,ratio,0,0);x.clearRect(0,0,w,h);
  var defs=[['close','#ffffff',1.8],['ma25','#55e69c',1.4],['ma75','#ffc857',1.4],['ma125','#5ab8ff',1.4],['ma200','#df7cff',1.4]],vals=[];
  rows.forEach(function(r){defs.forEach(function(d){var v=Number(r[d[0]]);if(isFinite(v))vals.push(v)})});if(!vals.length)return;var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),pad=Math.max((hi-lo)*.08,1);lo-=pad;hi+=pad;
  defs.forEach(function(d){x.strokeStyle=d[1];x.lineWidth=d[2];x.beginPath();var started=false;rows.forEach(function(r,i){var v=Number(r[d[0]]);if(!isFinite(v)){started=false;return}var px=6+i*(w-12)/Math.max(rows.length-1,1),py=8+(hi-v)*(h-20)/(hi-lo);if(started)x.lineTo(px,py);else{x.moveTo(px,py);started=true}});x.stroke()});
}
async function run(){
  var text=q.value.trim();if(!text){status.className='error';status.textContent='銘柄コードまたは会社名を入力してください。';return}
  b.disabled=true;result.hidden=true;status.className='muted';status.textContent='日足データを取得中…';
  try{
    var response=await fetch('/api/trend?q='+encodeURIComponent(text)+'&_='+Date.now(),{cache:'no-store'});
    var body=await response.text(),d;try{d=JSON.parse(body)}catch(e){throw new Error('サーバー応答を読み取れませんでした (HTTP '+response.status+')')}
    if(!response.ok||d.error)throw new Error(d.error||('取得に失敗しました (HTTP '+response.status+')'));
    document.getElementById('title').textContent=d.symbol+' '+d.name;document.getElementById('meta').textContent=d.as_of+' 終値 ¥'+fmt(d.price);document.getElementById('grade').textContent=d.stars+' '+d.label;document.getElementById('order').textContent='並び順: '+d.order;
    var html='';Object.keys(d.moving_averages).forEach(function(p){var m=d.moving_averages[p];html+='<div class="ma"><div>MA'+p+'</div><div class="v">¥'+fmt(m.value)+'</div><div class="'+cls(m.direction)+'">'+arrow(m.direction)+' '+m.direction+'（5日 '+(m.slope_5d_pct>=0?'+':'')+Number(m.slope_5d_pct).toFixed(2)+'%）</div><div class="muted" style="font-size:12px">株価との差 '+(m.price_distance_pct>=0?'+':'')+Number(m.price_distance_pct).toFixed(2)+'%</div></div>'});document.getElementById('grid').innerHTML=html;
    document.getElementById('source').textContent='データ元: '+d.source+'。売買判断ではなくテクニカル状態の可視化です。';result.hidden=false;status.className='ok';status.textContent='取得完了';draw(d.history);
  }catch(e){status.className='error';status.textContent=(e&&e.message)?e.message:'長期トレンド分析に失敗しました';}
  finally{b.disabled=false}
}
f.addEventListener('submit',function(e){e.preventDefault();run()});run();
})();
</script>
</html>'''


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _headers(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Stock-Monitor-Build", BUILD)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/trend":
                query = parse_qs(parsed.query).get("q", [""])[0]
                try:
                    payload = get_long_term_analysis(query)
                    status = 200
                except MarketHistoryError as exc:
                    payload = {"error": str(exc), "build": BUILD}
                    status = 502
                except Exception as exc:
                    payload = {"error": "長期トレンド分析で予期しないエラーが発生しました", "detail": type(exc).__name__, "build": BUILD}
                    status = 500
                body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self._headers(status, "application/json; charset=utf-8", body)
                self.wfile.write(body)
                return
            if parsed.path == "/health":
                body = json.dumps({"ok": True, "build": BUILD}).encode("utf-8")
                self._headers(200, "application/json; charset=utf-8", body)
                self.wfile.write(body)
                return
            body = HTML.encode("utf-8")
            self._headers(200, "text/html; charset=utf-8", body)
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    print(f"Stock Trend Monitor {BUILD}: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
