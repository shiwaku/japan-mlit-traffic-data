#!/usr/bin/env python3
"""
災害時 交通量異常検知

指定日の交通量を「前1週・前2週の同曜日」と比較し、都道府県別の中央値変化率を求める。
さらに平常日の同じ計算結果を分布として持ち、その分布内での位置（パーセンタイル）を
示すことで、観測された低下が通常のばらつきの範囲かどうかを判定する。

想定用途:
  広域の災害（台風・豪雨等）による交通の面的な影響範囲・時間推移の把握

判定できないこと（重要）:
  - 冠水地点など個別地点の特定。対象は直轄の一般国道のみで、冠水が起きやすい
    アンダーパス・県道・市道は元々データに含まれない
  - 「どの県が被災地か」の特定。本指標が捉えるのは「広範囲の悪天候で運転を
    控えた」ことであり、隣接する非被災県が同等以上に低下することがある

検証済みの検出例:
  2026/8/7  沖縄県 -82.6%（台風13号 沖縄本島直撃）
  2026/8/13 千葉県 -24.4%（令和8年8月千葉豪雨、関東の広範囲が同時に低下）

使い方:
  python scripts/analyze_disaster.py 20260813
  python scripts/analyze_disaster.py 20260813 20260814 --threshold -20
  python scripts/analyze_disaster.py 20260813 --csv docs/disaster_20260813.csv
"""

import argparse
import gzip
import json
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd

# ─── パス ──────────────────────────────────────────────────────────
BASE     = Path(__file__).parent.parent
DATA_5M  = BASE / "docs/data_5m"
STATIONS = BASE / "docs/stations.geojson"
PREF_URL = "https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson"
PREF_TMP = Path("/tmp/japan_pref.geojson")
PROJ_CRS = "EPSG:32654"  # sjoin_nearest 用（WGS84 / UTM zone 54N）

# ─── 判定パラメータ ────────────────────────────────────────────────
STEPS_PER_DAY   = 288    # 5分間隔 × 24時間
MIN_COVERAGE    = 0.8    # 有効コマがこの割合未満の観測点は欠測扱いで除外
MIN_BASE_VOLUME = 500    # 基準日の交通量がこれ未満の観測点は除外（小規模点のノイズ抑制）
MIN_STATIONS    = 5      # 都道府県の中央値を出すのに必要な最小観測点数
BASELINE_WEEKS  = (1, 2)  # 前1週・前2週の同曜日を基準とする


def parse_args():
    ap = argparse.ArgumentParser(description="災害時 交通量異常検知")
    ap.add_argument("dates", nargs="+", metavar="YYYYMMDD",
                    help="判定対象日（複数指定可）")
    ap.add_argument("--threshold", type=float, default=-20.0,
                    help="発火閾値（%%）。デフォルト -20")
    ap.add_argument("--top", type=int, default=10,
                    help="表示する下位県数。デフォルト 10")
    ap.add_argument("--csv", type=Path,
                    help="都道府県別の結果をCSV出力するパス")
    return ap.parse_args()


def load_pref_map() -> dict[str, str]:
    """観測点コード → 都道府県名 の対応を空間結合で作る"""
    if not PREF_TMP.exists():
        urllib.request.urlretrieve(PREF_URL, PREF_TMP)

    st = gpd.read_file(STATIONS).set_crs("EPSG:4326", allow_override=True)
    pref = (
        gpd.read_file(PREF_TMP)[["nam_ja", "geometry"]]
        .rename(columns={"nam_ja": "都道府県名"})
        .to_crs("EPSG:4326")
    )

    joined = gpd.sjoin(st, pref, how="left", predicate="within").drop(
        columns=["index_right"], errors="ignore"
    )
    # 沿岸・離島でポリゴン外に落ちる点は最近傍で補完
    unassigned = joined[joined["都道府県名"].isna()].index
    if len(unassigned):
        fb = gpd.sjoin_nearest(
            st.loc[unassigned].to_crs(PROJ_CRS),
            pref.to_crs(PROJ_CRS),
            how="left",
        ).drop(columns=["index_right"], errors="ignore")
        fb = fb[~fb.index.duplicated()]
        joined.loc[unassigned, "都道府県名"] = fb["都道府県名"].values

    return {
        str(int(r["観測点コード"])): r["都道府県名"]
        for _, r in joined.iterrows()
        if r["都道府県名"]
    }


_totals_cache: dict[str, dict[str, int] | None] = {}


def daily_totals(date: str) -> dict[str, int] | None:
    """観測点別の日交通量を返す。欠測が多い観測点は除外する。

    センサー障害で全コマ欠測した観測点は「交通量0」ではなく「データ無し」として
    扱う必要がある（通行止めとの取り違えを防ぐ）。
    """
    if date in _totals_cache:
        return _totals_cache[date]

    path = DATA_5M / f"{date}.json.gz"
    if not path.exists():
        _totals_cache[date] = None
        return None

    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)

    volume = defaultdict(int)
    valid_steps = defaultdict(int)
    for per_station in data.values():
        for code, values in per_station.items():
            if not values or all(v is None for v in values):
                continue
            volume[code] += sum(v for v in values if v is not None)
            valid_steps[code] += 1

    result = {
        code: vol
        for code, vol in volume.items()
        if valid_steps[code] >= STEPS_PER_DAY * MIN_COVERAGE
    }
    _totals_cache[date] = result
    return result


def pref_medians(date: str, pref_map: dict[str, str]):
    """指定日の都道府県別 中央値変化率と、全国の変化率一覧を返す"""
    target = daily_totals(date)
    if not target:
        return None, None

    dt = datetime.strptime(date, "%Y%m%d")
    baselines = [
        daily_totals((dt - timedelta(days=7 * w)).strftime("%Y%m%d"))
        for w in BASELINE_WEEKS
    ]
    baselines = [b for b in baselines if b]
    if not baselines:
        return None, None

    by_pref = defaultdict(list)
    all_rates = []
    for code, volume in target.items():
        base_values = [b[code] for b in baselines if code in b]
        # 全基準日に存在する観測点のみ（基準日ごとの母集団の揺れを防ぐ）
        if len(base_values) < len(baselines):
            continue
        base = sum(base_values) / len(base_values)
        if base < MIN_BASE_VOLUME:
            continue
        rate = 100 * (volume / base - 1)
        all_rates.append(rate)
        by_pref[pref_map.get(code, "不明")].append(rate)

    medians = {
        pref: (statistics.median(rates), len(rates))
        for pref, rates in by_pref.items()
        if len(rates) >= MIN_STATIONS
    }
    return medians, all_rates


def build_noise_distribution(exclude: set[str], pref_map) -> list[float]:
    """判定対象日を除く全日の都道府県別中央値を集め、平常時のばらつきとする

    注意: この分布には未知の災害日も混ざりうるため、誤検知率は保守的（過大）に出る。
    """
    noise = []
    for path in sorted(DATA_5M.glob("*.json.gz")):
        date = path.name.split(".")[0]
        if date in exclude:
            continue
        medians, _ = pref_medians(date, pref_map)
        if not medians:
            continue
        noise.extend(m for m, _ in medians.values())
    return sorted(noise)


def percentile_of(value: float, sorted_values: list[float]) -> float:
    """分布内での位置（下から何%か）"""
    if not sorted_values:
        return float("nan")
    below = sum(1 for v in sorted_values if v < value)
    return 100 * below / len(sorted_values)


def main():
    args = parse_args()

    print("【1】観測点の都道府県割当")
    pref_map = load_pref_map()
    print(f"   割当済み: {len(pref_map):,} 点")

    print("【2】平常日のばらつきを集計")
    noise = build_noise_distribution(set(args.dates), pref_map)
    if noise:
        print(f"   サンプル数: {len(noise):,} (県 × 日)")
        for q, label in [(0.01, " 1%"), (0.05, " 5%"), (0.5, "50%"), (0.95, "95%")]:
            print(f"     {label}分位: {noise[int(len(noise) * q)]:+6.1f}%")
        fire_rate = percentile_of(args.threshold, noise)
        print(f"   閾値 {args.threshold:+.0f}% の平常日発火率: {fire_rate:.2f}%"
              f" → 47県なら約 {47 * fire_rate / 100:.1f} 県/日 の誤発火")
    else:
        print("   警告: 比較可能な平常日がありません")

    rows = []
    for date in args.dates:
        print(f"\n【3】{date} の判定")
        medians, all_rates = pref_medians(date, pref_map)
        if not medians:
            print("   基準日（前1週・前2週の同曜日）のデータが不足しています")
            continue

        national = statistics.median(all_rates)
        print(f"   全国中央値: {national:+.1f}%  (n={len(all_rates):,})")
        print(f"   {'都道府県':<8}{'中央値':>9}{'点数':>6}{'平常日での位置':>16}  判定")

        for pref, (median, n) in sorted(medians.items(), key=lambda x: x[1][0])[:args.top]:
            pct = percentile_of(median, noise)
            fired = "発火" if median <= args.threshold else ""
            print(f"   {pref:<8}{median:>+8.1f}%{n:>6}{pct:>14.2f}%点  {fired}")
            rows.append({
                "日付": date, "都道府県名": pref, "中央値変化率": round(median, 2),
                "観測点数": n, "平常日分布での位置": round(pct, 2),
                "全国中央値": round(national, 2),
                "発火": median <= args.threshold,
            })

        fired = [p for p, (m, _) in medians.items() if m <= args.threshold]
        if len(fired) >= 3:
            print(f"   → {len(fired)}県が同時発火: {' '.join(fired)}")
            print("      隣接県がまとまって発火している場合、偶然のばらつきより"
                  "広域事象の可能性が高い")

    if args.csv and rows:
        import csv as _csv
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n   → {args.csv} ({len(rows)}行)")


if __name__ == "__main__":
    main()
