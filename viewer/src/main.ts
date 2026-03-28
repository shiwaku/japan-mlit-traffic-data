import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import * as pmtiles from "pmtiles";

// ── 定数 ────────────────────────────────────────────────
const BASE = import.meta.env.BASE_URL; // dev: "/", prod: "/japan-jartic-traffic-data/"
const DATA_5M_BASE = `${BASE}data_5m/`;
const DATA_1H_ALL = `${BASE}data_1h_all.json.gz`;
const GSI_PALE_STYLE = `${BASE}pale.json`;

// 5分データで利用可能な日付リスト（ビルド時に埋め込み or 動的取得）
// ここでは docs/data_5m/ 内のファイルを参照する日付を動的に生成
const INTERVAL_5M = 5; // 分
const STEPS_PER_DAY_5M = (24 * 60) / INTERVAL_5M; // 288
const STEPS_PER_DAY_1H = 24;

// 交通量の色分け閾値（台/期間）- 7色
// 統計的に設定（5分間データの観測値を考慮）
const THRESHOLDS_5M = [0, 5, 15, 30, 60, 100, 150];
const THRESHOLDS_1H = [0, 30, 100, 200, 400, 700, 1000];
const COLORS = [
  "#313695", // 0: 最小（濃い青）
  "#4575b4",
  "#74add1",
  "#fee090",
  "#f46d43",
  "#d73027",
  "#a50026", // 6: 最大（濃い赤）
];
const NO_DATA_COLOR = "#cccccc";

// ── 型定義 ──────────────────────────────────────────────
type TrafficValues = [
  number | null, // 0: 上り小型
  number | null, // 1: 上り大型
  number | null, // 2: 下り小型
  number | null  // 3: 下り大型
];
type TimeData = Record<string, Record<string, TrafficValues>>;

// ── 状態 ────────────────────────────────────────────────
let map: maplibregl.Map;
let currentMode: "5m" | "1h" = "1h";
let currentDir: "up" | "down" = "up";
let playing = false;
let playTimer: number | null = null;

// 5分データ: 日付→TimeData のキャッシュ
const cache5m = new Map<string, TimeData>();

// 1時間データ: 全期間
let data1h: TimeData | null = null;

// 1時間: タイムコード一覧（ソート済み）
let timecodes1h: string[] = [];

// 5分: 利用可能日付一覧
let available5mDates: string[] = [];

// 現在表示中のタイムコード→交通量マップ
let currentVolume: Record<string, number | null> = {};

// ── ユーティリティ ──────────────────────────────────────
function timecodeToLabel5m(step: number, date: string): string {
  const totalMin = step * INTERVAL_5M;
  const h = String(Math.floor(totalMin / 60)).padStart(2, "0");
  const m = String(totalMin % 60).padStart(2, "0");
  return `${date.slice(0, 4)}/${date.slice(4, 6)}/${date.slice(6, 8)} ${h}:${m}`;
}

function stepToTimecode5m(step: number, date: string): string {
  const totalMin = step * INTERVAL_5M;
  const h = String(Math.floor(totalMin / 60)).padStart(2, "0");
  const m = String(totalMin % 60).padStart(2, "0");
  return `${date}${h}${m}`;
}

function timecodeToLabel1h(tc: string): string {
  return `${tc.slice(0, 4)}/${tc.slice(4, 6)}/${tc.slice(6, 8)} ${tc.slice(8, 10)}:00`;
}

function volumeToColor(v: number | null, thresholds: number[]): string {
  if (v === null) return NO_DATA_COLOR;
  for (let i = thresholds.length - 1; i >= 0; i--) {
    if (v >= thresholds[i]) return COLORS[i];
  }
  return COLORS[0];
}

function getTrafficValue(vals: TrafficValues, dir: "up" | "down"): number | null {
  if (dir === "up") {
    const s = vals[0], l = vals[1];
    if (s === null && l === null) return null;
    return (s ?? 0) + (l ?? 0);
  } else {
    const s = vals[2], l = vals[3];
    if (s === null && l === null) return null;
    return (s ?? 0) + (l ?? 0);
  }
}

// ── 地図描画更新 ─────────────────────────────────────────
function updateMap(timeData: Record<string, TrafficValues>) {
  const thresholds = currentMode === "5m" ? THRESHOLDS_5M : THRESHOLDS_1H;
  const volume: Record<string, number | null> = {};
  for (const [code, vals] of Object.entries(timeData)) {
    volume[code] = getTrafficValue(vals, currentDir);
  }
  currentVolume = volume;

  if (!map.getLayer("stations")) return;

  // MapLibre の match 式で色を設定
  const colorExpr = [
    "match",
    ["to-string", ["get", "観測点コード"]],
    ...Object.entries(volume).flatMap(([code, v]) => [
      code,
      volumeToColor(v, thresholds),
    ]),
    NO_DATA_COLOR,
  ] as maplibregl.ExpressionSpecification;

  map.setPaintProperty("stations", "circle-color", colorExpr);
}

function clearMap() {
  if (!map.getLayer("stations")) return;
  map.setPaintProperty("stations", "circle-color", NO_DATA_COLOR);
}

// ── 5分データ読み込み ────────────────────────────────────
async function load5mData(date: string): Promise<TimeData | null> {
  if (cache5m.has(date)) return cache5m.get(date)!;
  try {
    const res = await fetch(`${DATA_5M_BASE}${date}.json.gz`);
    if (!res.ok) return null;
    const buf = await res.arrayBuffer();
    const ds = new DecompressionStream("gzip");
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(buf) as Uint8Array<ArrayBuffer>);
    writer.close();
    const reader = ds.readable.getReader();
    const chunks: Uint8Array<ArrayBuffer>[] = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value as Uint8Array<ArrayBuffer>);
    }
    const combined = new Uint8Array(chunks.reduce((acc, c) => acc + c.length, 0));
    let offset = 0;
    for (const c of chunks) { combined.set(c, offset); offset += c.length; }
    const text = new TextDecoder().decode(combined);
    const data: TimeData = JSON.parse(text);
    cache5m.set(date, data);
    // キャッシュは最大7日分
    if (cache5m.size > 7) {
      const oldest = cache5m.keys().next().value;
      if (oldest) cache5m.delete(oldest);
    }
    return data;
  } catch {
    return null;
  }
}

// ── 1時間データ読み込み ──────────────────────────────────
async function load1hAll(): Promise<void> {
  const loadingRow = document.getElementById("loading-row")!;
  const progress = document.getElementById("loading-progress")!;
  const label = document.getElementById("loading-label")!;
  loadingRow.classList.remove("hidden");
  label.textContent = "3ヶ月分データ読み込み中...";

  try {
    const res = await fetch(DATA_1H_ALL);
    if (!res.ok) throw new Error("fetch failed");
    const total = Number(res.headers.get("content-length") ?? 0);
    const reader = res.body!.getReader();
    const chunks: Uint8Array<ArrayBuffer>[] = [];
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value as Uint8Array<ArrayBuffer>);
      received += value.length;
      if (total > 0) {
        progress.style.width = `${Math.round((received / total) * 100)}%`;
      }
    }
    progress.style.width = "100%";
    label.textContent = "解凍中...";

    const combined = new Uint8Array(chunks.reduce((acc, c) => acc + c.length, 0));
    let offset = 0;
    for (const c of chunks) { combined.set(c, offset); offset += c.length; }

    const ds = new DecompressionStream("gzip");
    const writer = ds.writable.getWriter();
    writer.write(combined);
    writer.close();

    const drReader = ds.readable.getReader();
    const textChunks: Uint8Array<ArrayBuffer>[] = [];
    while (true) {
      const { done, value } = await drReader.read();
      if (done) break;
      textChunks.push(value as Uint8Array<ArrayBuffer>);
    }
    const combined2 = new Uint8Array(textChunks.reduce((acc, c) => acc + c.length, 0));
    let off2 = 0;
    for (const c of textChunks) { combined2.set(c, off2); off2 += c.length; }
    const text = new TextDecoder().decode(combined2);
    data1h = JSON.parse(text);
    timecodes1h = Object.keys(data1h!).sort();
    document.getElementById("slider-1h")!.setAttribute("max", String(timecodes1h.length - 1));
    label.textContent = `読み込み完了（${timecodes1h.length.toLocaleString()}ステップ）`;
    setTimeout(() => loadingRow.classList.add("hidden"), 2000);
  } catch (e) {
    label.textContent = "読み込みエラー";
    console.error(e);
  }
}

// ── 5分モード: 表示更新 ──────────────────────────────────
async function render5m() {
  const date = getSelectedDate();
  if (!date) return;
  const step = Number((document.getElementById("slider-5m") as HTMLInputElement).value);
  const tc = stepToTimecode5m(step, date);
  const label = document.getElementById("timecode-label-5m")!;
  label.textContent = timecodeToLabel5m(step, date).slice(11); // HH:MM

  const data = await load5mData(date);
  if (!data || !data[tc]) {
    clearMap();
    return;
  }
  updateMap(data[tc]);

  // プリフェッチ: 翌日
  prefetch5m(date, 1);
  prefetch5m(date, -1);
}

function prefetch5m(date: string, offset: number) {
  const d = new Date(
    Number(date.slice(0, 4)),
    Number(date.slice(4, 6)) - 1,
    Number(date.slice(6, 8)) + offset
  );
  const next = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  if (!cache5m.has(next)) load5mData(next);
}

// ── 1時間モード: 表示更新 ────────────────────────────────
function render1h() {
  if (!data1h) return;
  const step = Number((document.getElementById("slider-1h") as HTMLInputElement).value);
  const tc = timecodes1h[step];
  if (!tc) return;
  document.getElementById("timecode-label-1h")!.textContent = timecodeToLabel1h(tc);
  const timeData = data1h[tc];
  if (!timeData) { clearMap(); return; }
  updateMap(timeData);
}

// ── 凡例描画 ─────────────────────────────────────────────
function renderLegend() {
  const thresholds = currentMode === "5m" ? THRESHOLDS_5M : THRESHOLDS_1H;
  const unit = currentMode === "5m" ? "台/5分" : "台/時";
  const container = document.querySelector(".legend-scale")!;
  container.innerHTML = "";
  document.querySelector(".legend-title")!.textContent = `交通量（${unit}）`;

  thresholds.forEach((t, i) => {
    const next = thresholds[i + 1];
    const label = next ? `${t}〜${next}` : `${t}+`;
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `<span class="legend-color" style="background:${COLORS[i]}"></span>${label}`;
    container.appendChild(item);
  });

  // 欠測
  const nodata = document.createElement("div");
  nodata.className = "legend-item";
  nodata.innerHTML = `<span class="legend-color" style="background:${NO_DATA_COLOR}"></span>欠測`;
  container.appendChild(nodata);
}

// ── 日付選択 ─────────────────────────────────────────────
function getSelectedDate(): string {
  const y = (document.getElementById("sel-year") as HTMLSelectElement).value;
  const m = (document.getElementById("sel-month") as HTMLSelectElement).value;
  const d = (document.getElementById("sel-day") as HTMLSelectElement).value;
  return `${y}${m}${d}`;
}

function populateDateSelects() {
  if (available5mDates.length === 0) return;
  const dates = available5mDates;
  const years = [...new Set(dates.map((d) => d.slice(0, 4)))];
  const selYear = document.getElementById("sel-year") as HTMLSelectElement;
  const selMonth = document.getElementById("sel-month") as HTMLSelectElement;
  const selDay = document.getElementById("sel-day") as HTMLSelectElement;

  selYear.innerHTML = years.map((y) => `<option value="${y}">${y}</option>`).join("");
  selYear.value = years[years.length - 1];

  function updateMonths() {
    const y = selYear.value;
    const months = [...new Set(dates.filter((d) => d.startsWith(y)).map((d) => d.slice(4, 6)))];
    selMonth.innerHTML = months.map((m) => `<option value="${m}">${Number(m)}</option>`).join("");
    selMonth.value = months[months.length - 1];
    updateDays();
  }

  function updateDays() {
    const y = selYear.value;
    const m = selMonth.value;
    const days = dates.filter((d) => d.startsWith(y + m)).map((d) => d.slice(6, 8));
    selDay.innerHTML = days.map((d) => `<option value="${d}">${Number(d)}</option>`).join("");
    selDay.value = days[days.length - 1];
  }

  selYear.addEventListener("change", updateMonths);
  selMonth.addEventListener("change", updateDays);
  [selYear, selMonth, selDay].forEach((s) =>
    s.addEventListener("change", () => {
      stopPlay();
      render5m();
    })
  );

  updateMonths();
}

// ── 再生制御 ─────────────────────────────────────────────
function stopPlay() {
  if (playTimer !== null) {
    clearInterval(playTimer);
    playTimer = null;
  }
  playing = false;
  const btn5m = document.getElementById("btn-play-5m")!;
  const btn1h = document.getElementById("btn-play-1h")!;
  btn5m.textContent = "▶ 再生";
  btn1h.textContent = "▶ 再生";
}

function togglePlay5m() {
  if (playing) { stopPlay(); return; }
  playing = true;
  document.getElementById("btn-play-5m")!.textContent = "■ 停止";
  const speed = Number((document.getElementById("sel-speed-5m") as HTMLSelectElement).value);
  const slider = document.getElementById("slider-5m") as HTMLInputElement;
  playTimer = window.setInterval(async () => {
    let step = Number(slider.value) + 1;
    if (step > STEPS_PER_DAY_5M - 1) { stopPlay(); return; }
    slider.value = String(step);
    await render5m();
  }, Math.round(1000 / speed));
}

function togglePlay1h() {
  if (playing) { stopPlay(); return; }
  playing = true;
  document.getElementById("btn-play-1h")!.textContent = "■ 停止";
  const speed = Number((document.getElementById("sel-speed-1h") as HTMLSelectElement).value);
  const slider = document.getElementById("slider-1h") as HTMLInputElement;
  playTimer = window.setInterval(() => {
    let step = Number(slider.value) + 1;
    if (step > timecodes1h.length - 1) { stopPlay(); return; }
    slider.value = String(step);
    render1h();
  }, Math.round(1000 / speed));
}

// ── 利用可能日付の取得 ───────────────────────────────────
async function fetchAvailable5mDates(): Promise<string[]> {
  // stations.pmtiles と同じ場所にあると仮定
  // 実際には data_5m/ のファイルリストを別途用意するか、
  // 既知のレンジから生成する
  // ここでは data_1h_all.json.gz のタイムコードから日付を抽出
  // (1hデータが必ずあれば5mも同期間存在する前提)
  // 暫定: data_1h/ の日付範囲から推定（91日間）
  // index.json を別途生成するのが理想だが、ここでは /data_5m/index.json を参照
  try {
    const res = await fetch("/data_5m/index.json");
    if (res.ok) {
      const json = await res.json() as { dates: string[] };
      // 不正なエントリ（"index.json"等）を除外
      return json.dates.filter((d: string) => typeof d === "string" && /^\d{8}$/.test(d));
    }
  } catch { /* fallback */ }

  // フォールバック: 今日から31日前までの日付を生成
  const today = new Date();
  const dates: string[] = [];
  for (let i = 30; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    dates.push(
      `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`
    );
  }
  return dates;
}

// ── 地図初期化 ───────────────────────────────────────────
async function initMap() {
  // PMTiles プロトコルはMap作成前に登録（pale.jsonもpmtiles://を使うため）
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  map = new maplibregl.Map({
    container: "map",
    style: GSI_PALE_STYLE,
    center: [136.5, 36.5],
    zoom: 5,
    hash: true,
  });

  map.addControl(new maplibregl.NavigationControl(), "top-right");

  map.on("load", async () => {

    map.addSource("stations", {
      type: "vector",
      url: `pmtiles://${location.origin}${import.meta.env.BASE_URL}stations.pmtiles`,
    });

    map.addLayer({
      id: "stations",
      type: "circle",
      source: "stations",
      "source-layer": "stations",
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          5, 3,
          10, 6,
          14, 10,
        ],
        "circle-color": NO_DATA_COLOR,
        "circle-stroke-width": 0.5,
        "circle-stroke-color": "#fff",
        "circle-opacity": 0.85,
      },
    });

    // クリックでポップアップ
    map.on("click", "stations", (e) => {
      if (!e.features?.length) return;
      const props = e.features[0].properties;
      const code = String(props["観測点コード"]);
      const v = currentVolume[code];
      new maplibregl.Popup()
        .setLngLat(e.lngLat)
        .setHTML(
          `<b>観測点コード:</b> ${code}<br>` +
          `<b>都道府県コード:</b> ${props["都道府県コード"]}<br>` +
          `<b>${currentDir === "up" ? "上り" : "下り"}交通量:</b> ${v !== null && v !== undefined ? `${v} 台` : "欠測"}`
        )
        .addTo(map);
    });
    map.on("mouseenter", "stations", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "stations", () => { map.getCanvas().style.cursor = ""; });

    // 初期データロード（1時間モードをデフォルト）
    available5mDates = await fetchAvailable5mDates();
    populateDateSelects();
    renderLegend();
    await load1hAll();
    render1h();
  });
}

// ── イベント設定 ─────────────────────────────────────────
function setupEvents() {
  // モード切替
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mode = (btn as HTMLElement).dataset["mode"] as "5m" | "1h";
      if (mode === currentMode) return;
      stopPlay();
      currentMode = mode;
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("panel-5m")!.classList.toggle("hidden", mode !== "5m");
      document.getElementById("panel-1h")!.classList.toggle("hidden", mode !== "1h");
      renderLegend();
      if (mode === "1h") {
        if (!data1h) await load1hAll();
        render1h();
      } else {
        await render5m();
      }
    });
  });

  // 上り・下り切替
  document.querySelectorAll(".dir-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentDir = (btn as HTMLElement).dataset["dir"] as "up" | "down";
      document.querySelectorAll(".dir-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      if (currentMode === "5m") render5m();
      else render1h();
    });
  });

  // 5分スライダー
  const slider5m = document.getElementById("slider-5m") as HTMLInputElement;
  let slider5mTimer: number | null = null;
  slider5m.addEventListener("input", () => {
    const step = Number(slider5m.value);
    const date = getSelectedDate();
    document.getElementById("timecode-label-5m")!.textContent =
      timecodeToLabel5m(step, date).slice(11);
    if (slider5mTimer) clearTimeout(slider5mTimer);
    slider5mTimer = window.setTimeout(() => render5m(), 80);
  });

  // 1時間スライダー
  const slider1h = document.getElementById("slider-1h") as HTMLInputElement;
  let slider1hTimer: number | null = null;
  slider1h.addEventListener("input", () => {
    if (slider1hTimer) clearTimeout(slider1hTimer);
    slider1hTimer = window.setTimeout(() => render1h(), 50);
  });

  // 再生ボタン
  document.getElementById("btn-play-5m")!.addEventListener("click", togglePlay5m);
  document.getElementById("btn-play-1h")!.addEventListener("click", togglePlay1h);
}

// ── エントリポイント ─────────────────────────────────────
setupEvents();
initMap();
