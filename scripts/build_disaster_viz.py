#!/usr/bin/env python3
"""
災害時 交通量増減率マップ（HTML）生成

analyze_disaster.py が出力したCSVを読み、都道府県別の増減率を日ごとの
コロプレス小地図として並べたHTMLを生成する。台風経路のJSONを渡すと重ねて描画する。

出力: docs/disaster/index.html（自己完結。外部リソースを参照しない）

使い方:
  python scripts/analyze_disaster.py 20260807 20260808 20260809 20260810 \
      20260811 20260812 20260813 --csv /tmp/disaster_week.csv --top 47
  python scripts/build_disaster_viz.py --csv /tmp/disaster_week.csv \
      --track scripts/assets/typhoon_2613_track.json

配色:
  発散配色（赤=減少 / 青=増加、中間はグレー）。両アームとも OKLCH 明度が単調で
  左右対称になるよう選定済み。ライト／ダークで別々のステップを持つ。
"""

import argparse
import csv
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

import geopandas as gpd

BASE     = Path(__file__).parent.parent
PREF_URL = "https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson"
PREF_TMP = Path("/tmp/japan_pref.geojson")
OUT      = BASE / "docs/disaster/index.html"

SIMPLIFY_TOL = 0.012  # 度。県境の形が保たれる範囲で頂点を間引く
COORD_ND     = 3      # 座標の小数桁（約100m精度。HTMLサイズ削減）


def parse_args():
    ap = argparse.ArgumentParser(description="災害時 交通量増減率マップ生成")
    ap.add_argument("--csv", type=Path, required=True,
                    help="analyze_disaster.py --csv の出力")
    ap.add_argument("--track", type=Path,
                    help="台風経路JSON（省略時は経路を描かない）")
    ap.add_argument("--title", default="2026年8月 全国交通量の増減率と台風13号の経路")
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    return ap.parse_args()


def load_prefectures() -> dict:
    """県境ポリゴンを簡素化し、座標を丸めたGeoJSONを返す"""
    if not PREF_TMP.exists():
        urllib.request.urlretrieve(PREF_URL, PREF_TMP)
    pref = gpd.read_file(PREF_TMP)[["nam_ja", "geometry"]].rename(
        columns={"nam_ja": "name"})
    pref["geometry"] = pref.geometry.simplify(SIMPLIFY_TOL, preserve_topology=True)

    def round_coords(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], COORD_ND), round(c[1], COORD_ND)]
        return [round_coords(x) for x in c]

    feats = []
    for _, row in pref.iterrows():
        g = row.geometry.__geo_interface__
        feats.append({
            "type": "Feature",
            "properties": {"name": row["name"]},
            "geometry": {"type": g["type"], "coordinates": round_coords(g["coordinates"])},
        })
    return {"type": "FeatureCollection", "features": feats}


def load_rates(path: Path):
    rates, counts, national = defaultdict(dict), defaultdict(dict), {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = r["日付"]
            rates[d][r["都道府県名"]] = float(r["中央値変化率"])
            counts[d][r["都道府県名"]] = int(r["観測点数"])
            national[d] = float(r["全国中央値"])
    return rates, counts, national


TEMPLATE = r"""<title>__TITLE__</title>
<style>
  .viz-root{
    --surface-1:#fcfcfb; --page:#f9f9f7;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
    --grid:#e1e0d9; --border:rgba(11,11,11,0.10);
    --neutral:#f0efec;
    --d4:#a52322; --d3:#e34948; --d2:#ef8b8a; --d1:#f7c3c2;
    --i1:#b7d3f6; --i2:#6da7ec; --i3:#2a78d6; --i4:#184f95;
    --track:#0b0b0b; --track-halo:#fcfcfb;
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    background:var(--page); color:var(--text-primary);
    margin:0 auto; padding:28px 20px 56px; max-width:1180px;
  }
  @media (prefers-color-scheme:dark){
    .viz-root{
      --surface-1:#1a1a19; --page:#0d0d0d;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
      --grid:#2c2c2a; --border:rgba(255,255,255,0.10);
      --neutral:#383835;
      --d4:#f2908f; --d3:#d14a49; --d2:#93312f; --d1:#5c2726;
      --i1:#17304f; --i2:#1c5cab; --i3:#3987e5; --i4:#86b6ef;
      --track:#ffffff; --track-halo:#0d0d0d;
    }
  }
  :root[data-theme="dark"] .viz-root{
      --surface-1:#1a1a19; --page:#0d0d0d;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
      --grid:#2c2c2a; --border:rgba(255,255,255,0.10);
      --neutral:#383835;
      --d4:#f2908f; --d3:#d14a49; --d2:#93312f; --d1:#5c2726;
      --i1:#17304f; --i2:#1c5cab; --i3:#3987e5; --i4:#86b6ef;
      --track:#ffffff; --track-halo:#0d0d0d;
  }
  :root[data-theme="light"] .viz-root{
      --surface-1:#fcfcfb; --page:#f9f9f7;
      --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
      --grid:#e1e0d9; --border:rgba(11,11,11,0.10);
      --neutral:#f0efec;
      --d4:#a52322; --d3:#e34948; --d2:#ef8b8a; --d1:#f7c3c2;
      --i1:#b7d3f6; --i2:#6da7ec; --i3:#2a78d6; --i4:#184f95;
      --track:#0b0b0b; --track-halo:#fcfcfb;
  }
  h1{font-size:22px; line-height:1.35; margin:0 0 6px; font-weight:650;}
  .sub{color:var(--text-secondary); font-size:13.5px; line-height:1.6; margin:0 0 4px; max-width:76ch;}
  .note{color:var(--muted); font-size:12px; line-height:1.6; margin:10px 0 0; max-width:82ch;}
  .legend{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:20px 0 6px;
    padding:12px 14px; background:var(--surface-1); border:1px solid var(--border); border-radius:10px;}
  .legend .lab{font-size:12px; color:var(--text-secondary); white-space:nowrap;}
  .swatches{display:flex; align-items:stretch; gap:2px;}
  .sw{width:38px; height:16px; border-radius:2px;}
  .ticks{display:flex; gap:2px; margin-top:3px;}
  .ticks span{width:38px; font-size:10.5px; color:var(--muted); text-align:center;
    font-variant-numeric:tabular-nums;}
  .legend-track{display:flex; align-items:center; gap:7px; margin-left:auto;}
  .grid{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-top:14px;}
  @media (max-width:900px){ .grid{grid-template-columns:repeat(2,1fr);} }
  @media (max-width:520px){ .grid{grid-template-columns:1fr;} }
  .cell{background:var(--surface-1); border:1px solid var(--border); border-radius:10px;
    padding:10px 10px 6px; position:relative;}
  .cell h3{margin:0 0 1px; font-size:13px; font-weight:640;}
  .cell .meta{margin:0 0 4px; font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums;}
  .tag{display:inline-block; font-size:10.5px; padding:1px 6px; border-radius:999px;
    border:1px solid var(--border); color:var(--text-secondary); margin-left:6px; vertical-align:1px;}
  svg{display:block; width:100%; height:auto; overflow:hidden;}
  path.pref{stroke:var(--surface-1); stroke-width:.5; cursor:pointer;}
  path.pref:hover{stroke:var(--text-primary); stroke-width:1.4;}
  path.nodata{fill:var(--grid);}
  .tip{position:fixed; pointer-events:none; z-index:50; background:var(--surface-1);
    color:var(--text-primary); border:1px solid var(--border); border-radius:8px;
    padding:8px 10px; font-size:12px; line-height:1.5; opacity:0;
    transition:opacity .1s; box-shadow:0 4px 14px rgba(0,0,0,.16); max-width:230px;}
  .tip b{font-weight:640;} .tip .v{font-variant-numeric:tabular-nums;}
  details{margin-top:26px; background:var(--surface-1); border:1px solid var(--border);
    border-radius:10px; padding:12px 14px;}
  summary{cursor:pointer; font-size:13px; font-weight:600;}
  .scroll{overflow-x:auto; margin-top:10px;}
  table{border-collapse:collapse; font-size:12px; width:100%; min-width:640px;}
  th,td{text-align:right; padding:5px 9px; border-bottom:1px solid var(--grid);
    font-variant-numeric:tabular-nums; white-space:nowrap;}
  th:first-child,td:first-child{text-align:left; font-variant-numeric:normal;}
  th{color:var(--text-secondary); font-weight:600; position:sticky; top:0; background:var(--surface-1);}
  .src{margin-top:22px; font-size:11.5px; color:var(--muted); line-height:1.7;}
  .src a{color:inherit;}
</style>

<div class="viz-root">
  <h1>__TITLE__</h1>
  <p class="sub">
    都道府県ごとの1日交通量を<strong>前1週・前2週の同曜日の平均</strong>と比べた変化率（観測点別変化率の中央値）。
    独立した2つの気象事象が、交通量の落ち込みとして別々の場所・別々の日に現れている。
  </p>
  <p class="sub">
    <strong>8/7 沖縄</strong>：台風13号(DOLPHIN)が沖縄本島へ最接近 →&nbsp;<span class="v">-82.7%</span>。
    <strong>8/13 千葉</strong>：線状降水帯による豪雨 →&nbsp;<span class="v">-24.4%</span>（関東各県が同時に低下）。
    台風13号は沖縄通過後に西進しており、千葉の豪雨とは無関係。
  </p>

  <div class="legend">
    <span class="lab">交通量の変化</span>
    <div>
      <div class="swatches" id="sw"></div>
      <div class="ticks" id="tk"></div>
    </div>
    <div class="legend-track" id="legend-track" hidden>
      <svg width="46" height="14" aria-hidden="true">
        <path d="M2,11 C14,3 30,3 44,7" fill="none" stroke="var(--track)"
              stroke-width="2" stroke-linecap="round" stroke-dasharray="1 4"/>
        <circle cx="30" cy="4.6" r="3.6" fill="var(--track)" stroke="var(--track-halo)" stroke-width="2"/>
      </svg>
      <span class="lab">台風13号の経路（●は当日12&nbsp;JSTの中心）</span>
    </div>
  </div>

  <div class="grid" id="grid"></div>

  <p class="note">
    読み方：色は「いつもの同じ曜日と比べてどれだけ交通量が変わったか」。<span class="v">0%</span>付近＝平常。
    赤いほど減少＝人が運転を控えた／通れなかった。<strong>お盆期間にあたるため全国的にはむしろ増加傾向</strong>で、
    その中で赤く沈む県が際立つ。灰色は観測点が5点未満で中央値を出せない県。
  </p>
  <p class="note">
    この指標が捉えるのは「広域の悪天候で運転が減ったこと」であり、冠水地点の特定はできない。
    対象は直轄の一般国道のみで、冠水が起きやすいアンダーパス・県道・市道は元データに含まれない。
    センサー全欠測を「交通量0」と誤認しないよう、有効コマ8割未満の観測点は除外している。
  </p>

  <details>
    <summary>データtable（都道府県 × 日）</summary>
    <div class="scroll"><table id="tbl"></table></div>
  </details>

  <p class="src">
    交通量：<a href="https://www.jartic-open-traffic.org/">国土交通省 交通量データ（JARTIC提供）</a>（参考値）／
    台風経路：<a href="https://www.data.jma.go.jp/typhoon/position_table/table2026.html">気象庁 台風位置表</a>／
    県境：<a href="https://github.com/dataofjapan/land">dataofjapan/land</a><br>
    本データは正式な交通量調査結果ではなく、機器障害・気象等による欠測を含みうる参考値。
  </p>
  <div class="tip" id="tip" role="status" aria-live="polite"></div>
</div>

<script>
const D = window.__DATA__;

// ─ 発散スケール（赤=減少 / 青=増加、中間グレー）。明度単調性を検証済み
const BINS = [-40,-25,-15,-5,5,15,25,40];
const FILLS = ['--d4','--d3','--d2','--d1','--neutral','--i1','--i2','--i3','--i4'];
const TICKS = ['-40','-25','-15','-5','+5','+15','+25','+40'];
const fillFor = v => `var(${FILLS[BINS.filter(b => v >= b).length]})`;

const sw = document.getElementById('sw'), tk = document.getElementById('tk');
FILLS.forEach(f => { const d = document.createElement('div');
  d.className='sw'; d.style.background=`var(${f})`; sw.appendChild(d); });
tk.appendChild(document.createElement('span'));
TICKS.forEach(t => { const s=document.createElement('span'); s.textContent=t; tk.appendChild(s); });

// ─ 投影（日本全域を1枚に収める簡易メルカトル）
const W = 258, H = 208;
const LON0=127.0, LON1=146.2, LAT0=30.2, LAT1=45.7;
const merc = lat => Math.log(Math.tan(Math.PI/4 + lat*Math.PI/360));
const MY0 = merc(LAT0), MY1 = merc(LAT1);
const px = lon => (lon-LON0)/(LON1-LON0)*W;
const py = lat => H - (merc(lat)-MY0)/(MY1-MY0)*H;

// 沖縄は本土から遠いので別枠・別スケールで拡大。左上（日本海側）は本土が来ないので重ならない
const OKI = {lon0:127.3, lon1:128.6, lat0:25.95, lat1:27.30, x:5, y:14, w:52, h:62};
const okiX = lon => OKI.x + (lon-OKI.lon0)/(OKI.lon1-OKI.lon0)*OKI.w;
const okiY = lat => OKI.y + OKI.h - (lat-OKI.lat0)/(OKI.lat1-OKI.lat0)*OKI.h;
const inMainBox = (lo, la) => lo >= LON0 && lo <= LON1 && la >= LAT0 && la <= LAT1;
const inOkiBox  = (lo, la) =>
  lo >= OKI.lon0 && lo <= OKI.lon1 && la >= OKI.lat0 && la <= OKI.lat1;

// リングを描くのは頂点が対象範囲に完全に収まる場合のみ。
// （沖縄insetに先島諸島、本土地図に南西諸島が紛れ込んで枠外へ飛ぶのを防ぐ）
function pathFor(geom, oki){
  const X = oki ? okiX : px, Y = oki ? okiY : py;
  const within = oki ? inOkiBox : inMainBox;
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
  let d = '';
  for (const poly of polys){
    for (const ring of poly){
      if (ring.length < 4) continue;
      if (!ring.every(([lo, la]) => within(lo, la))) continue;
      let seg = '', n = 0;
      for (const [lo, la] of ring)
        seg += (n++ ? 'L' : 'M') + X(lo).toFixed(1) + ',' + Y(la).toFixed(1);
      if (n > 2) d += seg + 'Z';
    }
  }
  return d;
}
const inOki = n => n === '沖縄県';
const PATHS = D.geo.features.map(f => ({
  name: f.properties.name,
  d: pathFor(f.geometry, inOki(f.properties.name))
}));

// ─ 台風経路（JSTへ変換）
const trackPts = (D.track || []).map(t => {
  let h = t.hourUTC + 9, day = t.day;
  if (h >= 24){ h -= 24; day += 1; }
  return {day, hour:h, lat:t.lat, lon:t.lon, pressure:t.pressure};
});
if (trackPts.length) document.getElementById('legend-track').hidden = false;

// 範囲内の連続区間だけを線分に。範囲外へ出たらそこで線を切る
// （切らずに繋ぐと、範囲外の座標が投影されてパネル外へ飛ぶ）
function segsFor(pts, within, X, Y){
  const segs = []; let cur = [];
  for (const p of pts){
    if (within(p.lon, p.lat)) cur.push(`${X(p.lon).toFixed(1)},${Y(p.lat).toFixed(1)}`);
    else { if (cur.length > 1) segs.push(cur); cur = []; }
  }
  if (cur.length > 1) segs.push(cur);
  return segs.map(s => 'M' + s.join('L'));
}
function trackUpTo(day){
  const pts = trackPts.filter(p => p.day <= day);
  const lines = [
    ...segsFor(pts, inMainBox, px, py),
    ...segsFor(pts, inOkiBox, okiX, okiY),
  ];
  const noon = pts.filter(p => p.day === day).reduce((a,p) =>
    (a && Math.abs(a.hour-12) <= Math.abs(p.hour-12)) ? a : p, null);
  let marker = null;
  if (noon){
    if (inMainBox(noon.lon, noon.lat))     marker = [px(noon.lon), py(noon.lat)];
    else if (inOkiBox(noon.lon, noon.lat)) marker = [okiX(noon.lon), okiY(noon.lat)];
  }
  return {lines, marker, noon};
}

const FLAG = D.flags || {};
const fmtDate = s => `${+s.slice(4,6)}月${+s.slice(6,8)}日`;
const WD = ['日','月','火','水','木','金','土'];
const wdOf = s => WD[new Date(+s.slice(0,4), +s.slice(4,6)-1, +s.slice(6,8)).getDay()];

const tip = document.getElementById('tip');
const show = (e, html) => {
  tip.innerHTML = html; tip.style.opacity = 1;
  const r = tip.getBoundingClientRect();
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
};
const hide = () => tip.style.opacity = 0;
const NS = 'http://www.w3.org/2000/svg';

const grid = document.getElementById('grid');
D.dates.forEach(date => {
  const rates = D.rates[date], ns = D.n[date];
  const day = +date.slice(6,8);
  const {lines, marker, noon} = trackUpTo(day);

  const cell = document.createElement('div');
  cell.className = 'cell';
  cell.innerHTML =
    `<h3>${fmtDate(date)}<span style="color:var(--muted);font-weight:400">（${wdOf(date)}）</span>` +
      (FLAG[date] ? `<span class="tag">${FLAG[date]}</span>` : '') + `</h3>` +
    `<p class="meta">全国中央値 ${D.national[date] >= 0 ? '+' : ''}${D.national[date].toFixed(1)}%</p>`;

  const svg = document.createElementNS(NS,'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role','img');
  svg.setAttribute('aria-label', `${fmtDate(date)}の都道府県別交通量変化率`);

  PATHS.forEach(p => {
    const v = rates[p.name];
    const el = document.createElementNS(NS,'path');
    el.setAttribute('d', p.d);
    el.setAttribute('class', 'pref' + (v === undefined ? ' nodata' : ''));
    if (v !== undefined) el.setAttribute('fill', fillFor(v));
    el.addEventListener('pointerenter', e => show(e,
      `<b>${p.name}</b>　${fmtDate(date)}<br>` +
      (v === undefined
        ? '観測点5点未満のためデータなし'
        : `変化率 <span class="v">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</span><br>` +
          `<span style="color:var(--text-secondary)">観測点 ${ns[p.name]}点</span>`)));
    el.addEventListener('pointermove', e => show(e, tip.innerHTML));
    el.addEventListener('pointerleave', hide);
    svg.appendChild(el);
  });

  const box = document.createElementNS(NS,'rect');
  box.setAttribute('x',OKI.x-3); box.setAttribute('y',OKI.y-3);
  box.setAttribute('width',OKI.w+6); box.setAttribute('height',OKI.h+6);
  box.setAttribute('fill','none'); box.setAttribute('stroke','var(--grid)');
  box.setAttribute('rx','3'); svg.appendChild(box);
  const lbl = document.createElementNS(NS,'text');
  lbl.setAttribute('x',OKI.x-1); lbl.setAttribute('y',OKI.y-6);
  lbl.setAttribute('fill','var(--muted)'); lbl.setAttribute('font-size','7.5');
  lbl.textContent = '沖縄（拡大）'; svg.appendChild(lbl);

  lines.forEach(d => {
    const halo = document.createElementNS(NS,'path');
    halo.setAttribute('d', d); halo.setAttribute('fill','none');
    halo.setAttribute('stroke','var(--track-halo)'); halo.setAttribute('stroke-width','4');
    halo.setAttribute('stroke-linecap','round'); svg.appendChild(halo);
    const ln = document.createElementNS(NS,'path');
    ln.setAttribute('d', d); ln.setAttribute('fill','none');
    ln.setAttribute('stroke','var(--track)'); ln.setAttribute('stroke-width','2');
    ln.setAttribute('stroke-linecap','round'); ln.setAttribute('stroke-dasharray','1 4');
    svg.appendChild(ln);
  });
  if (marker){
    const c = document.createElementNS(NS,'circle');
    c.setAttribute('cx',marker[0]); c.setAttribute('cy',marker[1]); c.setAttribute('r','4');
    c.setAttribute('fill','var(--track)');
    c.setAttribute('stroke','var(--track-halo)'); c.setAttribute('stroke-width','2');
    c.style.cursor='pointer';
    c.addEventListener('pointerenter', e => show(e,
      `<b>台風13号 DOLPHIN</b><br>${fmtDate(date)} ${String(noon.hour).padStart(2,'0')}時JST<br>` +
      `<span class="v">${noon.pressure} hPa</span>`));
    c.addEventListener('pointerleave', hide);
    svg.appendChild(c);
  }
  cell.appendChild(svg);
  grid.appendChild(cell);
});

// ─ table view（色だけに頼らないための代替表現）
const names = [...new Set(D.dates.flatMap(d => Object.keys(D.rates[d])))].sort();
document.getElementById('tbl').innerHTML =
  '<thead><tr><th>都道府県</th>' + D.dates.map(d => `<th>${fmtDate(d)}</th>`).join('') + '</tr></thead>' +
  '<tbody>' + names.map(n => '<tr><td>' + n + '</td>' + D.dates.map(d => {
      const v = D.rates[d][n];
      return `<td>${v === undefined ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'}</td>`;
    }).join('') + '</tr>').join('') + '</tbody>';
</script>
"""

# 注釈を入れる日（事象が判明しているもの）
FLAGS = {
    "20260807": "台風13号 沖縄本島最接近",
    "20260813": "千葉 線状降水帯",
}


def main():
    args = parse_args()

    print("【1】県境ポリゴン取得・簡素化")
    geo = load_prefectures()
    print(f"   {len(geo['features'])} 県")

    print("【2】増減率CSV読み込み")
    rates, counts, national = load_rates(args.csv)
    dates = sorted(rates.keys())
    print(f"   {len(dates)} 日分: {dates[0]} 〜 {dates[-1]}")

    track = []
    if args.track:
        raw = json.loads(args.track.read_text(encoding="utf-8"))
        pts = raw["points"] if isinstance(raw, dict) else raw
        days = {int(d[6:8]) for d in dates}
        track = [p for p in pts if p["day"] in days or p["day"] in {min(days) - 1}]
        print(f"【3】台風経路: {len(track)} 点")

    payload = {
        "geo": geo, "rates": rates, "n": counts, "national": national,
        "dates": dates, "track": track, "flags": FLAGS,
    }
    html = (f"<script>window.__DATA__="
            f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};</script>\n"
            + TEMPLATE.replace("__TITLE__", args.title))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"   → {args.out} ({args.out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
