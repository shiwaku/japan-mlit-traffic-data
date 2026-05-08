#!/usr/bin/env python3
"""
GW 2026 交通量増減率分析
2026/4/29–5/6 vs 通常期（前1週・前2週 同曜日の平均）

出力:
  docs/gw_stations.parquet  観測点別増減率 GeoParquet
  docs/gw_pref.parquet      都道府県別増減率 GeoParquet
  docs/gw_stations.pmtiles  観測点 PMTiles  (tippecanoe)
  docs/gw_pref.pmtiles      都道府県 PMTiles (tippecanoe)
  docs/gw_stations.qml      QGIS 点スタイル
  docs/gw_pref.qml          QGIS 面スタイル
"""

import argparse
import csv
import gzip
import io
import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

# ─── パス ──────────────────────────────────────────────────────────
BASE     = Path(__file__).parent.parent
DATA_1H  = BASE / "docs/data_1h_all.json.gz"
STATIONS = BASE / "docs/stations.geojson"
PREF_URL = "https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson"
PREF_TMP = Path("/tmp/japan_pref.geojson")
OUT      = BASE / "docs/gw"

# ─── コマンドライン引数 ────────────────────────────────────────────
_ap = argparse.ArgumentParser(description="GW 2026 交通量増減率分析")
_ap.add_argument("--shoshiki2-only", action="store_true",
                 help="様式2（常設トラカン）のみ対象（CCTVトラカン除外）")
ARGS = _ap.parse_args()
SUFFIX = "_s2" if ARGS.shoshiki2_only else ""

S2_CACHE = Path("/tmp/gw_shoshiki2_codes.json")


def get_shoshiki2_codes() -> set[str]:
    """JARTIC API から様式2の観測点コード一覧を取得（キャッシュあり）"""
    if S2_CACHE.exists():
        return set(json.loads(S2_CACHE.read_text()))
    print("   様式2コード: API 取得中...")
    cql = (
        "道路種別=3 AND 時間コード>=202604290000 AND 時間コード<=202604290000"
        " AND BBOX(ジオメトリ,122.0,24.0,154.0,46.0,'EPSG:4326')"
    )
    params = urllib.parse.urlencode({
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "t_travospublic_measure_1h",
        "cql_filter": cql,
        "outputFormat": "csv", "exceptions": "application/json",
        "srsName": "EPSG:4326",
    })
    url = f"https://api.jartic-open-traffic.org/geoserver?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        raw_bytes = resp.read()
    inner = raw_bytes.decode("utf-8").strip('"')
    reader = csv.DictReader(io.StringIO(inner.replace("\\r\\n", "\n")))
    codes = {row["常時観測点コード"] for row in reader if row.get("常時観測点コード")}
    S2_CACHE.write_text(json.dumps(sorted(codes)))
    print(f"   様式2コード: {len(codes):,} 点 → キャッシュ保存")
    return codes


# ─── GW期間（2026/4/29–5/6 の8日間）──────────────────────────────
GW_DAYS = [datetime(2026, 4, 29) + timedelta(days=i) for i in range(8)]


def day_hours(d: datetime) -> list[str]:
    """指定日の1時間タイムスタンプ24本を返す（YYYYMMDDhh00形式）"""
    return [f"{d:%Y%m%d}{h:02d}00" for h in range(24)]


# ─── 1. データ読み込み ─────────────────────────────────────────────
print("1/5  データ読み込み...")
with gzip.open(DATA_1H) as f:
    raw: dict[str, dict[str, list]] = json.load(f)
print(f"   タイムステップ数: {len(raw):,}")

# ─── 2. 観測点ごとの増減率計算 ─────────────────────────────────────
print("2/5  増減率計算...")

gw_ts = [ts for d in GW_DAYS for ts in day_hours(d)]
bl_ts = [
    ts
    for d in GW_DAYS
    for cd in (d - timedelta(weeks=1), d - timedelta(weeks=2))
    for ts in day_hours(cd)
]

all_codes: set[str] = set().union(*(d.keys() for d in raw.values()))

if ARGS.shoshiki2_only:
    s2_codes = get_shoshiki2_codes()
    before = len(all_codes)
    all_codes = all_codes & s2_codes
    print(f"   様式2フィルタ: {before:,} → {len(all_codes):,} 点")


def vtotal(vals) -> float | None:
    """[上小, 上大, 下小, 下大] の合計。None 含む場合は None。"""
    if vals is None or any(v is None for v in vals):
        return None
    return float(sum(vals))


rows = []
for code in sorted(all_codes):
    gw_v = [t for ts in gw_ts if (t := vtotal(raw.get(ts, {}).get(code))) is not None]
    bl_v = [t for ts in bl_ts if (t := vtotal(raw.get(ts, {}).get(code))) is not None]
    if not gw_v or not bl_v:
        continue
    g, b = float(np.mean(gw_v)), float(np.mean(bl_v))
    rows.append({
        "観測点コード":     int(code),
        "gw_hourly_avg":   round(g, 1),
        "bl_hourly_avg":   round(b, 1),
        "change_rate":     round((g / b - 1) * 100, 2) if b > 0 else float("nan"),
        "gw_hours":        len(gw_v),
        "bl_hours":        len(bl_v),
    })

df = pd.DataFrame(rows)
print(f"   有効観測点: {len(df):,} 点")

# バックデータ（GW期間・基準期間の時刻別生値）を保存
valid_codes = set(df["観測点コード"].astype(str))
bd_rows = []
for label, ts_list in [("GW", gw_ts), ("基準", bl_ts)]:
    for ts in ts_list:
        for code, vals in raw.get(ts, {}).items():
            if code not in valid_codes:
                continue
            if vals is None or any(v is None for v in vals):
                continue
            bd_rows.append({
                "観測点コード": int(code),
                "時間コード":   ts,
                "種別":         label,
                "上り小型":     vals[0],
                "上り大型":     vals[1],
                "下り小型":     vals[2],
                "下り大型":     vals[3],
                "合計":         int(sum(vals)),
            })
OUT.mkdir(exist_ok=True)
bd_df = pd.DataFrame(bd_rows)
bd_df.to_csv(OUT / f"gw_backdata{SUFFIX}.csv.gz", index=False, compression="gzip")
print(f"   バックデータ: {len(bd_df):,} 行 → gw_backdata{SUFFIX}.csv.gz")

# ─── 3. 空間結合（観測点 → 都道府県）─────────────────────────────
print("3/5  空間結合...")

if not PREF_TMP.exists():
    urllib.request.urlretrieve(PREF_URL, PREF_TMP)

st_gdf = gpd.read_file(STATIONS)
st_gdf["観測点コード"] = st_gdf["観測点コード"].astype(int)
# stations.geojson の都道府県コードは JARTIC 独自コードのため rename して衝突を回避
st_gdf = st_gdf.rename(columns={"都道府県コード": "jartic_pref_code"})

pref = (
    gpd.read_file(PREF_TMP)[["nam_ja", "id", "geometry"]]
    .rename(columns={"nam_ja": "都道府県名", "id": "都道府県コード"})
    .to_crs("EPSG:4326")
)
# sjoin_nearest 用の投影座標系（WGS84 / UTM zone 54N）
PROJ_CRS = "EPSG:32654"

merged = st_gdf.merge(df, on="観測点コード", how="inner").to_crs("EPSG:4326")

# within で結合 → 未割当点は sjoin_nearest で補完（沿岸・離島対応）
joined = gpd.sjoin(merged, pref, how="left", predicate="within").drop(
    columns=["index_right"], errors="ignore"
)
unassigned = joined[joined["都道府県名"].isna()].index
if len(unassigned):
    fb = gpd.sjoin_nearest(
        merged.loc[unassigned].to_crs(PROJ_CRS),
        pref[["都道府県名", "都道府県コード", "geometry"]].to_crs(PROJ_CRS),
        how="left",
    ).drop(columns=["index_right"], errors="ignore")
    joined.loc[unassigned, ["都道府県名", "都道府県コード"]] = fb[["都道府県名", "都道府県コード"]].values
    print(f"   sjoin_nearest で補完: {len(unassigned)} 点")

print(f"   未割当: {joined['都道府県名'].isna().sum()} 点")

# ─── 4. 都道府県集計 ─────────────────────────────────────────────
pref_stats = (
    joined.dropna(subset=["都道府県名", "change_rate"])
    .groupby(["都道府県名", "都道府県コード"], as_index=False)
    .agg(
        観測点数=("change_rate", "count"),
        change_rate=("change_rate", "mean"),
        gw_hourly_avg=("gw_hourly_avg", "mean"),
        bl_hourly_avg=("bl_hourly_avg", "mean"),
    )
    .round({"change_rate": 2, "gw_hourly_avg": 1, "bl_hourly_avg": 1})
)
pref_poly = pref.merge(pref_stats, on=["都道府県名", "都道府県コード"], how="left")

# ─── 5. GeoParquet 出力 ──────────────────────────────────────────
print("4/5  GeoParquet 出力...")
OUT.mkdir(exist_ok=True)

st_out = joined[[
    "観測点コード", "都道府県名", "都道府県コード",
    "gw_hourly_avg", "bl_hourly_avg", "change_rate",
    "gw_hours", "bl_hours", "geometry",
]].copy()

st_out.to_parquet(OUT / f"gw_stations{SUFFIX}.parquet", index=False)
pref_poly.to_parquet(OUT / f"gw_pref{SUFFIX}.parquet", index=False)
print(f"   → gw_stations{SUFFIX}.parquet ({len(st_out):,} 点)")
print(f"   → gw_pref{SUFFIX}.parquet ({len(pref_poly)} 都道府県)")

# ─── 6. PMTiles 変換（tippecanoe）────────────────────────────────
print("5/5  PMTiles 変換...")


def to_pmtiles(gdf: gpd.GeoDataFrame, out_path: Path, layer: str, zmin: int, zmax: int):
    tmp = out_path.with_suffix(".geojson")
    gdf.to_crs("EPSG:4326").to_file(tmp, driver="GeoJSON")
    cmd = [
        "tippecanoe", "-o", str(out_path), f"--layer={layer}",
        f"-Z{zmin}", f"-z{zmax}", "-r1", "--force", str(tmp),
    ]
    if not shutil.which("tippecanoe"):
        print(f"   tippecanoe 未検出。手動実行:")
        print(f"   $ {' '.join(cmd)}")
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        tmp.unlink(missing_ok=True)
        print(f"   → {out_path.name}")
    else:
        print(f"   エラー: {r.stderr[:300]}")


to_pmtiles(st_out, OUT / f"gw_stations{SUFFIX}.pmtiles", "gw_stations", 5, 14)
to_pmtiles(pref_poly, OUT / f"gw_pref{SUFFIX}.pmtiles", "gw_pref", 4, 10)

# ─── 7. QML 出力 ─────────────────────────────────────────────────
# 増減率の色分け区分（青=減少、赤=増加）
BREAKS = [
    (-9999,  -10, "#2166ac", "≤ −10%"),
    (  -10,    0, "#92c5de", "−10% 〜 0%"),
    (    0,   10, "#fddbc7", "0% 〜 +10%"),
    (   10,   20, "#f4a582", "+10% 〜 +20%"),
    (   20,   30, "#d6604d", "+20% 〜 +30%"),
    (   30, 9999, "#b2182b", "> +30%"),
]


def _hex_rgba(h: str, alpha: int = 230) -> str:
    h = h.lstrip("#")
    return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{alpha}"


def _range_xml(breaks: list[tuple]) -> str:
    return "\n      ".join(
        f'<range symbol="{i}" lower="{lo}" upper="{hi}" '
        f'label="{lb.replace("&","&amp;").replace(">","&gt;")}" render="true"/>'
        for i, (lo, hi, _, lb) in enumerate(breaks)
    )


def _point_symbol(i: int, color: str) -> str:
    return f"""\
    <symbol type="marker" name="{i}" alpha="1" clip_to_extent="1" force_rhr="0">
      <data_defined_properties>
        <Option type="Map">
          <Option name="name" value="" type="QString"/>
          <Option name="properties"/>
          <Option name="type" value="collection" type="QString"/>
        </Option>
      </data_defined_properties>
      <layer class="SimpleMarker" pass="0" enabled="1" locked="0">
        <Option type="Map">
          <Option name="color" value="{_hex_rgba(color)}" type="QString"/>
          <Option name="outline_color" value="255,255,255,160" type="QString"/>
          <Option name="outline_width" value="0.2" type="QString"/>
          <Option name="size" value="2.5" type="QString"/>
          <Option name="size_unit" value="MM" type="QString"/>
          <Option name="name" value="circle" type="QString"/>
        </Option>
      </layer>
    </symbol>"""


def _fill_symbol(i: int, color: str) -> str:
    return f"""\
    <symbol type="fill" name="{i}" alpha="1" clip_to_extent="1" force_rhr="0">
      <data_defined_properties>
        <Option type="Map">
          <Option name="name" value="" type="QString"/>
          <Option name="properties"/>
          <Option name="type" value="collection" type="QString"/>
        </Option>
      </data_defined_properties>
      <layer class="SimpleFill" pass="0" enabled="1" locked="0">
        <Option type="Map">
          <Option name="color" value="{_hex_rgba(color)}" type="QString"/>
          <Option name="outline_color" value="80,80,80,200" type="QString"/>
          <Option name="outline_width" value="0.15" type="QString"/>
        </Option>
      </layer>
    </symbol>"""


def make_qml(attr: str, breaks: list[tuple], symbol_fn) -> str:
    ranges   = _range_xml(breaks)
    symbols  = "\n".join(symbol_fn(i, c) for i, (*_, c, __) in enumerate(breaks))
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28.0" styleCategories="Symbology">
  <renderer-v2 attr="{attr}" type="graduatedSymbol" graduatedMethod="GraduatedColor"
               symbollevels="0" enableorderby="0" referencescale="-1">
    <ranges>
      {ranges}
    </ranges>
    <symbols>
{symbols}
    </symbols>
  </renderer-v2>
</qgis>"""


(OUT / f"gw_stations{SUFFIX}.qml").write_text(
    make_qml("change_rate", BREAKS, _point_symbol), encoding="utf-8"
)
(OUT / f"gw_pref{SUFFIX}.qml").write_text(
    make_qml("change_rate", BREAKS, _fill_symbol), encoding="utf-8"
)
print(f"   → gw_stations{SUFFIX}.qml, gw_pref{SUFFIX}.qml")

# ─── サマリー出力 ─────────────────────────────────────────────────
print("\n=== GW 2026 全国サマリー ===")
valid = joined["change_rate"].dropna()
print(f"全観測点平均増減率: {valid.mean():+.1f}%  (n={len(valid):,})")
print(f"増加点数: {(valid > 0).sum():,} / {len(valid):,} ({(valid > 0).mean()*100:.0f}%)")

print("\n都道府県別 増加率トップ10:")
top10 = pref_stats.nlargest(10, "change_rate")[["都道府県名", "change_rate", "観測点数"]]
for _, r in top10.iterrows():
    print(f"  {r['都道府県名']}: {r['change_rate']:+.1f}%  ({int(r['観測点数'])} 点)")

print("\n都道府県別 増加率ワースト5:")
bot5 = pref_stats.nsmallest(5, "change_rate")[["都道府県名", "change_rate", "観測点数"]]
for _, r in bot5.iterrows():
    print(f"  {r['都道府県名']}: {r['change_rate']:+.1f}%  ({int(r['観測点数'])} 点)")
