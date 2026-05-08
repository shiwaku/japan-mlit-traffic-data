# docs/ ディレクトリ構成

このディレクトリは GitHub Pages として公開されるファイルを管理する。

## 通常交通量ビューワー（ルート直下）

| ファイル | 役割 |
|---|---|
| `index.html` | メインビューワー（MapLibre、5分間/1時間スライダー） |
| `pale.json` | 地図スタイル（国土地理院ベクタータイル） |
| `assets/` | Vite ビルド成果物（JS/CSS） |

### gitignore 対象（S3 配信）

| ファイル | 役割 |
|---|---|
| `stations.pmtiles` | 観測点ベクタータイル |
| `stations.geojson` | 観測点マスタ |
| `data_5m/` | 5分間交通量（日別 JSON.gz） |
| `data_1h_all.json.gz` | 1時間交通量（全期間累積） |

## GW 増減率分析（`gw/` サブディレクトリ）

| ファイル | 役割 |
|---|---|
| `gw/index.html` | GW 増減率マップビューワー（MapLibre、様式2のみ・有効1,010点） |
| `gw/METHODOLOGY.md` | 分析手法・前提条件の説明 |
| `gw/gw_stations_s2.pmtiles` | 観測点増減率タイル・様式2のみ（有効1,010点）★ウェブマップ使用 |
| `gw/gw_pref_s2.pmtiles` | 都道府県増減率タイル・様式2のみ ★ウェブマップ使用 |
| `gw/gw_stations.pmtiles` | 観測点増減率タイル（様式2+4、有効1,633点） |
| `gw/gw_pref.pmtiles` | 都道府県増減率タイル（様式2+4） |
| `gw/gw_*.qml` | QGIS スタイルファイル |

### gitignore 対象（大容量）

| ファイル | 役割 |
|---|---|
| `gw/gw_*.parquet` | GeoParquet（QGIS/Python 分析用） |
| `gw/gw_backdata*.csv.gz` | 時刻別生データ（バックデータ） |

## ビューワー URL（GitHub Pages）

- 通常ビューワー: `https://<user>.github.io/<repo>/`
- GW マップ: `https://<user>.github.io/<repo>/gw/`
