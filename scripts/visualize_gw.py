#!/usr/bin/env python3
"""
GW 2026 交通量増減率マップ生成
出力: docs/gw_map.png
"""
import json
import os
import time
import urllib.request
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.cm import ScalarMappable
import numpy as np

warnings.filterwarnings("ignore")

# ─── フォント（Windows フォントを WSL から読み込み）─────────────────
_WIN = "/mnt/c/Windows/Fonts"
if os.path.exists(_WIN):
    for _fn in os.listdir(_WIN):
        if _fn.lower().endswith((".ttf", ".otf")):
            try:
                mpl.font_manager.fontManager.addfont(os.path.join(_WIN, _fn))
            except Exception:
                pass

_JP_FONT = next(
    (f for f in ["Noto Sans JP", "Yu Gothic", "Meiryo", "IPAexGothic", "Droid Sans Fallback"]
     if f in {t.name for t in mpl.font_manager.fontManager.ttflist}),
    None,
)
if _JP_FONT:
    mpl.rcParams["font.family"] = _JP_FONT

# ─── パス設定 ────────────────────────────────────────────────────────
BASE     = Path(__file__).parent.parent
ST_PAR   = BASE / "docs/gw_stations.parquet"
PREF_PAR = BASE / "docs/gw_pref.parquet"
OUT_PNG  = BASE / "docs/gw_map.png"
CACHE    = Path("/tmp/gw_city_cache.json")
CRS_P      = "EPSG:4326"     # WGS84 geographic（lon/lat）で描画
BG         = "#f5f0e8"
SEA        = "#c8dced"

# 地図表示範囲・アスペクト
MAP_XLIM   = (126.5, 150.5)                          # 経度範囲
MAP_YLIM   = (30.0,  46.5)                           # 緯度範囲
MAP_ASPECT = 1.0 / np.cos(np.radians(36.0))          # 等矩形図法補正 ≈1.236

# ─── 地域ブロック ────────────────────────────────────────────────────
REGIONS = {
    "北海道": ["北海道"],
    "東北":   ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東":   ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    "甲信越": ["山梨県", "長野県", "新潟県"],
    "北陸":   ["富山県", "石川県", "福井県"],
    "中部":   ["静岡県", "愛知県", "三重県", "岐阜県"],
    "近畿":   ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国":   ["鳥取県", "島根県", "岡山県", "広島県", "山口県"],
    "四国":   ["徳島県", "香川県", "愛媛県", "高知県"],
    "九州":   ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"],
    "沖縄":   ["沖縄県"],
}

# ─── カラースキーム ───────────────────────────────────────────────────
PT_BRKS = [-9999, -10, 0, 10, 20, 30, 9999]
PT_COLS = ["#2166ac", "#92c5de", "#fddbc7", "#f4a582", "#d6604d", "#b2182b"]
PT_LABS = ["≤−10%", "−10〜0%", "0〜+10%", "+10〜+20%", "+20〜+30%", ">+30%"]


def pt_clr(r) -> str:
    try:
        v = float(r)
    except (TypeError, ValueError):
        return "#cccccc"
    if np.isnan(v):
        return "#cccccc"
    for i, b in enumerate(PT_BRKS[1:]):
        if v < b:
            return PT_COLS[i]
    return PT_COLS[-1]


PNORM = TwoSlopeNorm(vmin=-10, vcenter=0, vmax=35)
PCMAP = mpl.colormaps["RdBu_r"]


def pref_clr(r) -> str:
    try:
        v = float(r)
    except (TypeError, ValueError):
        return "#e0e0e0"
    if np.isnan(v):
        return "#e0e0e0"
    return mcolors.to_hex(PCMAP(PNORM(v)))


# ─── GSI 逆ジオコーディング ──────────────────────────────────────────
def get_city(lon: float, lat: float, cache: dict) -> str:
    key = f"{round(lon, 4)},{round(lat, 4)}"
    if key in cache:
        return cache[key]
    try:
        url = (
            f"https://mreversegeocoder.gsi.go.jp/reverse-geocoder/"
            f"LonLatToAddress?lat={lat}&lon={lon}"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            d = json.load(resp)
        name = d["results"]["lv01Nm"]
    except Exception:
        name = ""
    cache[key] = name
    time.sleep(0.12)
    return name


# ─── 1. データ読み込み ────────────────────────────────────────────────
print("1/4  データ読み込み...")
st_wgs  = gpd.read_parquet(ST_PAR)
prf_wgs = gpd.read_parquet(PREF_PAR)

# ─── 2. 地域集計 & 逆ジオコーディング ───────────────────────────────
print("2/4  地域集計・逆ジオコーディング...")
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
p2r   = {p: r for r, ps in REGIONS.items() for p in ps}
st_wgs["region"] = st_wgs["都道府県名"].map(p2r)

rinfo: dict[str, dict] = {}
for rname in REGIONS:
    sub = st_wgs[st_wgs["region"] == rname].dropna(subset=["change_rate"])
    if not len(sub):
        continue
    top3  = sub.nlargest(3, "change_rate")
    items = []
    for _, row in top3.iterrows():
        city = get_city(row.geometry.x, row.geometry.y, cache)
        items.append((city or "−", row["change_rate"]))
    rinfo[rname] = {"avg": sub["change_rate"].mean(), "n": len(sub), "top3": items}
    print(f"  {rname}: {rinfo[rname]['avg']:+.1f}%")

CACHE.write_text(json.dumps(cache, ensure_ascii=False))

# ─── 3. 投影変換 & 図サイズ計算 ────────────────────────────────────────
print("3/4  地図描画...")
stp    = st_wgs.to_crs(CRS_P)
prfp   = prf_wgs.to_crs(CRS_P)
outl   = prfp.dissolve()
main   = prfp[prfp["都道府県名"] != "沖縄県"]
ok_gdf = prfp[prfp["都道府県名"] == "沖縄県"]

mb = main.total_bounds           # minx, miny, maxx, maxy
W, H = mb[2] - mb[0], mb[3] - mb[1]

# データ範囲（箱スペース込み）からアスペクト比を計算し、figure幅を決定
DATA_XL = mb[0] - 0.30 * W
DATA_XR = mb[2] + 0.55 * W
DATA_YL = mb[1] - 0.03 * H
DATA_YR = mb[3] + 0.05 * H
DATA_ASPECT = (DATA_XR - DATA_XL) / (DATA_YR - DATA_YL)
AX_W, AX_H = 0.76, 0.88
FIG_H = 21.0
FIG_W = FIG_H * DATA_ASPECT * AX_H / AX_W   # 正しい figure 幅

# 地域重心（接続線用）
rcent: dict[str, tuple] = {}
for rname, prefs in REGIONS.items():
    sub = prfp[prfp["都道府県名"].isin(prefs)]
    if not len(sub):
        continue
    c = sub.dissolve().centroid.iloc[0]
    rcent[rname] = (c.x, c.y)

# ─── 4. 描画 ──────────────────────────────────────────────────────────
fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=BG)
ax  = fig.add_axes([0.01, 0.06, AX_W, AX_H], facecolor=SEA)
ax.set_aspect("equal")
ax.axis("off")

# 都道府県塗り分け
prfp["_c"] = prfp["change_rate"].apply(pref_clr)
prfp.plot(ax=ax, color=prfp["_c"], edgecolor="#888888", linewidth=0.25)
outl.plot(ax=ax, facecolor="none", edgecolor="#444444", linewidth=0.6)

# 観測点
stp["_c"] = stp["change_rate"].apply(pt_clr)
valid = stp.dropna(subset=["change_rate"])
ax.scatter(
    valid.geometry.x, valid.geometry.y,
    c=valid["_c"], s=14, linewidths=0.25,
    edgecolors="white", zorder=5, alpha=0.9,
)

# 軸範囲（事前計算済み）
ax.set_xlim(DATA_XL, DATA_XR)
ax.set_ylim(DATA_YL, DATA_YR)
xl, xr = ax.get_xlim()
yl, yr = ax.get_ylim()
TW, TH = xr - xl, yr - yl

# ─── 沖縄インセット ──────────────────────────────────────────────────
ax_ok = ax.inset_axes([0.01, 0.01, 0.12, 0.09])
ax_ok.set_facecolor(SEA)
ok_gdf["_c"] = ok_gdf["change_rate"].apply(pref_clr)
ok_gdf.plot(ax=ax_ok, color=ok_gdf["_c"], edgecolor="#888888", linewidth=0.3)
ok_st = stp[stp["都道府県名"] == "沖縄県"].dropna(subset=["change_rate"])
ax_ok.scatter(ok_st.geometry.x, ok_st.geometry.y,
              c=ok_st["_c"], s=8, lw=0.2, edgecolors="white", zorder=5)
ob = ok_gdf.total_bounds
ax_ok.set_xlim(ob[0] - 20000, ob[2] + 20000)
ax_ok.set_ylim(ob[1] - 20000, ob[3] + 20000)
ax_ok.set_aspect("equal")
ax_ok.axis("off")
for sp in ax_ok.spines.values():
    sp.set_visible(True)
    sp.set_linewidth(0.7)
    sp.set_edgecolor("#777777")
if "沖縄" in rinfo:
    ax_ok.set_title(f"沖縄  {rinfo['沖縄']['avg']:+.1f}%", fontsize=7, pad=3)

# ─── 注釈ボックス ────────────────────────────────────────────────────
BW    = 0.32 * W    # ボックス幅（~244,000 m）
RH    = 0.028 * H   # 行高（~51,000 m）
PAD   = 0.008 * W   # 文字左マージン


def draw_box(rname: str, bx: float, by: float):
    """by = ボックス上端の Y 座標"""
    if rname not in rinfo:
        return
    info = rinfo[rname]
    top3 = info["top3"]
    bh   = RH * (1 + len(top3))
    avg  = info["avg"]
    hclr = pref_clr(avg)
    tclr = "white" if abs(avg) > 3 else "#222222"

    # 接続線（ボックス中央 → 地域重心）
    if rname in rcent and rname != "沖縄":
        cx = float(np.clip(rcent[rname][0], mb[0], mb[2]))
        cy = float(np.clip(rcent[rname][1], mb[1], mb[3]))
        ax.plot(
            [bx + BW * 0.5, cx], [by - bh * 0.5, cy],
            color="#aaaaaa", lw=0.7, zorder=4, alpha=0.8,
        )

    # ヘッダ行
    ax.add_patch(mpatches.FancyBboxPatch(
        (bx, by - RH), BW, RH,
        boxstyle="square,pad=0", facecolor=hclr,
        edgecolor="#666666", lw=0.5, zorder=10,
    ))
    ax.text(bx + PAD, by - RH * 0.5,
            f"{rname}   {avg:+.1f}%",
            ha="left", va="center", fontsize=7.5, fontweight="bold",
            color=tclr, zorder=11)

    # 観測点行
    for i, (city, rate) in enumerate(top3):
        ry  = by - RH * (i + 2)
        bg  = "#ffffff" if i % 2 == 0 else "#f5f3ef"
        ax.add_patch(mpatches.FancyBboxPatch(
            (bx, ry), BW, RH,
            boxstyle="square,pad=0", facecolor=bg,
            edgecolor="#666666", lw=0.5, zorder=10,
        ))
        label = (city[:8] + "…") if len(city) > 9 else city
        ax.text(bx + PAD, ry + RH * 0.5, label,
                ha="left", va="center", fontsize=6.5, color="#333333", zorder=11)
        ax.text(bx + BW * 0.96, ry + RH * 0.5, f"{rate:+.0f}%",
                ha="right", va="center", fontsize=6.5, fontweight="bold",
                color="#c00000", zorder=11)


# ── ボックス配置 ──
# 右側（太平洋側）: 北海道・東北・関東・甲信越
RX = mb[2] + 0.06 * W
draw_box("北海道", RX, yr - 0.00 * TH)
draw_box("東北",   RX, yr - 0.22 * TH)
draw_box("関東",   RX, yr - 0.43 * TH)
draw_box("甲信越", RX, yr - 0.62 * TH)

# 左側（日本海側）: 北陸・中国・九州
LX = xl + 0.01 * TW
draw_box("北陸",   LX, mb[3] - 0.10 * H)
draw_box("中国",   LX, mb[3] - 0.36 * H)
draw_box("九州",   LX, mb[3] - 0.63 * H)

# 南部内部（紀伊半島周辺・四国）
draw_box("近畿",   mb[0] + 0.15 * W, mb[1] + 0.38 * H)
draw_box("中部",   mb[0] + 0.43 * W, mb[1] + 0.30 * H)
draw_box("四国",   mb[0] + 0.22 * W, mb[1] + 0.14 * H)

# ─── 凡例（観測点）────────────────────────────────────────────────────
LGX = xr - BW * 1.02
LGY = yl + 0.04 * TH
LGW = BW * 0.95
LGH = TH * 0.23

ax.add_patch(mpatches.FancyBboxPatch(
    (LGX, LGY), LGW, LGH,
    boxstyle="square,pad=0", facecolor="white",
    edgecolor="#aaaaaa", lw=0.6, zorder=10,
))
ax.text(LGX + LGW * 0.05, LGY + LGH * 0.94,
        "観測点の増減率",
        fontsize=8, fontweight="bold", va="top", zorder=11)
for i, (col, lab) in enumerate(zip(PT_COLS, PT_LABS)):
    dy = LGY + LGH * (0.80 - i * 0.13)
    ax.plot(LGX + LGW * 0.11, dy, "o", ms=6,
            color=col, mec="white", mew=0.4, zorder=11)
    ax.text(LGX + LGW * 0.22, dy, lab,
            fontsize=7, va="center", zorder=11)

# 都道府県カラーバー
sm  = ScalarMappable(cmap=PCMAP, norm=PNORM)
sm.set_array([])
cax = ax.inset_axes([0.77, 0.02, 0.20, 0.022])
cb  = fig.colorbar(sm, cax=cax, orientation="horizontal")
cb.set_label("都道府県塗り分け (%)", fontsize=6.5)
cb.ax.tick_params(labelsize=6)
cb.set_ticks([-5, 0, 10, 20, 30])
cb.set_ticklabels(["-5%", "±0%", "+10%", "+20%", "+30%"])

# ─── タイトル・出典 ───────────────────────────────────────────────────
ax.text(xl + 0.01 * TW, yr - 0.004 * TH,
        "大型連休 2026\n国道・交通量増減率マップ",
        fontsize=15, fontweight="bold", va="top", color="#222222",
        linespacing=1.4, zorder=15)
ax.text(xl + 0.01 * TW, yr - 0.085 * TH,
        "2026/4/29–5/6 vs 通常期（前2週同曜日平均）── 上下合算・GW期間平均",
        fontsize=7.5, color="#555555", va="top", zorder=15)

fig.text(
    0.02, 0.018,
    "国土交通省 API 機能による交通量（参考値）を加工して作成（API公開全1,978観測点）\n"
    "地方ブロック区分は地方整備局管轄に準拠。塗り分けは各地方ブロック内の観測点平均増減率。",
    fontsize=6.5, color="#777777", va="bottom",
)

# ─── 保存 ───────────────────────────────────────────────────────────
print(f"4/4  保存: {OUT_PNG}")
fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight", facecolor=BG)
plt.close()
print("完了")
