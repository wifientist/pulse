import { RefreshCw, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getTrends, startBoost } from "../api/endpoints";
import type {
  TrendPoint,
  TrendResponse,
  WirelessTrendPoint,
  WirelessTrendSeries,
} from "../api/types";
import { useFilterStore } from "../store/filter";
import { useSnapshotStore } from "../store/snapshot";
import { type ApResolver, buildApResolver } from "../utils/apMatch";

type RangeKey = "1m" | "5m" | "10m" | "30m" | "1h" | "6h" | "24h" | "7d";

const RANGE_OPTIONS: { key: RangeKey; label: string; ms: number }[] = [
  { key: "1m", label: "1 m", ms: 60 * 1000 },
  { key: "5m", label: "5 m", ms: 5 * 60 * 1000 },
  { key: "10m", label: "10 m", ms: 10 * 60 * 1000 },
  { key: "30m", label: "30 m", ms: 30 * 60 * 1000 },
  { key: "1h", label: "1 h", ms: 60 * 60 * 1000 },
  { key: "6h", label: "6 h", ms: 6 * 60 * 60 * 1000 },
  { key: "24h", label: "24 h", ms: 24 * 60 * 60 * 1000 },
  { key: "7d", label: "7 d", ms: 7 * 24 * 60 * 60 * 1000 },
];

const AUTO_REFRESH_MS = 5000;
const AUTO_REFRESH_MAX_RANGE_MS = 30 * 60 * 1000;

function formatTs(ms: number, short: boolean): string {
  const d = new Date(ms);
  if (short) {
    return d.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  }
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatMs(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(2)} ms`;
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(2)}%`;
}

export default function TrendsPage() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const filterSelected = useFilterStore((s) => s.selected);

  const activeAgents = useMemo(
    () =>
      (snapshot?.agents ?? [])
        .filter((a) => a.state !== "revoked")
        .slice()
        .sort((a, b) => a.hostname.localeCompare(b.hostname)),
    [snapshot?.agents],
  );

  const prefilledSource = filterSelected[0] ?? activeAgents[0]?.agent_uid ?? "";
  const prefilledTarget = filterSelected[1] ?? activeAgents[1]?.agent_uid ?? "";

  const [sourceUid, setSourceUid] = useState<string>(prefilledSource);
  const [targetUid, setTargetUid] = useState<string>(prefilledTarget);
  const [range, setRange] = useState<RangeKey>("5m");
  const [data, setData] = useState<TrendResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [boostDuration, setBoostDuration] = useState<number>(300);
  const [boostBusy, setBoostBusy] = useState<boolean>(false);

  // Is either agent currently boosted? Reflected on the button so it doesn't
  // look like a no-op when one side is already humming.
  const boostedUids = useMemo(
    () =>
      new Set(
        (snapshot?.boosts ?? [])
          .filter((b) => b.expires_at > Date.now())
          .map((b) => b.agent_uid),
      ),
    [snapshot?.boosts],
  );
  const sourceBoosted = boostedUids.has(sourceUid);
  const targetBoosted = boostedUids.has(targetUid);

  const agentIdByUid = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of snapshot?.agents ?? []) m.set(a.agent_uid, a.id);
    return m;
  }, [snapshot?.agents]);

  useEffect(() => {
    setSourceUid((prev) => prev || prefilledSource);
    setTargetUid((prev) => prev || prefilledTarget);
  }, [prefilledSource, prefilledTarget]);

  const load = useCallback(async () => {
    if (!sourceUid || !targetUid || sourceUid === targetUid) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const ms = RANGE_OPTIONS.find((r) => r.key === range)?.ms ?? 5 * 60 * 1000;
      const now = Date.now();
      const r = await getTrends(sourceUid, targetUid, now - ms, now);
      setData(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [sourceUid, targetUid, range]);

  useEffect(() => {
    load();
  }, [load]);

  const onBoostBoth = async () => {
    const srcId = agentIdByUid.get(sourceUid);
    const tgtId = agentIdByUid.get(targetUid);
    if (srcId == null || tgtId == null) return;
    setBoostBusy(true);
    setError(null);
    try {
      await Promise.all([
        startBoost(srcId, boostDuration),
        startBoost(tgtId, boostDuration),
      ]);
      // Kick an immediate refresh so new raw-tier samples show up without
      // waiting for the next auto-refresh tick.
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "boost failed");
    } finally {
      setBoostBusy(false);
    }
  };

  // Auto-refresh for short ranges so a live boost flows in without manual refresh.
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    const windowMs =
      RANGE_OPTIONS.find((r) => r.key === range)?.ms ?? Infinity;
    if (windowMs > AUTO_REFRESH_MAX_RANGE_MS) return;
    const t = setInterval(() => loadRef.current(), AUTO_REFRESH_MS);
    return () => clearInterval(t);
  }, [range]);

  const short =
    range === "1m" ||
    range === "5m" ||
    range === "10m" ||
    range === "30m" ||
    range === "1h" ||
    range === "6h" ||
    range === "24h";

  const chartData = useMemo(
    () =>
      (data?.points ?? []).map((p: TrendPoint) => ({
        ts: p.ts_ms,
        tsLabel: formatTs(p.ts_ms, short),
        rtt_avg: p.rtt_avg_ms,
        rtt_p50: p.rtt_p50_ms,
        rtt_p95: p.rtt_p95_ms,
        rtt_p99: p.rtt_p99_ms,
        loss: p.loss_pct,
        jitter: p.jitter_ms,
        sent: p.sent,
        lost: p.lost,
      })),
    [data?.points, short],
  );

  const hostnameOf = (uid: string) =>
    activeAgents.find((a) => a.agent_uid === uid)?.hostname ?? uid.slice(0, 8);

  // Prefix-tolerant resolver — Ruckus + co. vary the last octet of the BSSID
  // per radio/SSID, so the same physical AP can appear as multiple BSSIDs.
  const apResolver = useMemo(
    () => buildApResolver(snapshot?.access_points ?? []),
    [snapshot?.access_points],
  );

  const rttTooltip = (props: { active?: boolean; payload?: readonly unknown[] }) => {
    const { active, payload } = props;
    if (!active || !payload || !payload.length) return null;
    const p = (payload[0] as { payload: (typeof chartData)[number] }).payload;
    return (
      <div className="bg-white border border-slate-200 rounded shadow-lg text-xs p-2 space-y-0.5">
        <div className="font-semibold text-slate-900">{p.tsLabel}</div>
        <div>
          avg: <span className="font-mono">{formatMs(p.rtt_avg)}</span>
        </div>
        <div>
          p50: <span className="font-mono">{formatMs(p.rtt_p50)}</span>
        </div>
        <div>
          p95: <span className="font-mono">{formatMs(p.rtt_p95)}</span>
        </div>
        <div>
          p99: <span className="font-mono">{formatMs(p.rtt_p99)}</span>
        </div>
        <div className="pt-0.5 text-slate-400">
          sent {p.sent} · lost {p.lost}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">Trends</h1>
        <p className="text-sm text-slate-500">
          Historical connectivity for a source→target pair. Ranges ≤2h use raw
          samples (1 Hz during boost). 2h–24h uses minute aggregates. Longer uses
          hour aggregates.
        </p>
      </div>

      <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
          <label className="block">
            <div className="text-xs text-slate-500 mb-1">Source</div>
            <select
              value={sourceUid}
              onChange={(e) => setSourceUid(e.target.value)}
              className="w-full border border-slate-200 rounded px-2 py-1"
            >
              <option value="">— pick source —</option>
              {activeAgents.map((a) => (
                <option key={a.agent_uid} value={a.agent_uid}>
                  {a.hostname}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <div className="text-xs text-slate-500 mb-1">Target</div>
            <select
              value={targetUid}
              onChange={(e) => setTargetUid(e.target.value)}
              className="w-full border border-slate-200 rounded px-2 py-1"
            >
              <option value="">— pick target —</option>
              {activeAgents.map((a) => (
                <option key={a.agent_uid} value={a.agent_uid}>
                  {a.hostname}
                </option>
              ))}
            </select>
          </label>
          <div className="md:col-span-2">
            <div className="text-xs text-slate-500 mb-1">Range</div>
            <div className="flex items-center gap-1 flex-wrap">
              {RANGE_OPTIONS.map((r) => (
                <button
                  key={r.key}
                  onClick={() => setRange(r.key)}
                  className={
                    r.key === range
                      ? "px-2.5 py-1 rounded text-xs bg-sky-500 text-white"
                      : "px-2.5 py-1 rounded text-xs bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"
                  }
                >
                  {r.label}
                </button>
              ))}
              <div className="ml-auto flex items-center gap-1">
                <select
                  value={boostDuration}
                  onChange={(e) => setBoostDuration(Number(e.target.value))}
                  className="border border-slate-200 rounded px-1.5 py-1 text-xs"
                  title="Boost duration"
                >
                  <option value={300}>5 min</option>
                  <option value={1200}>20 min</option>
                  <option value={3600}>60 min</option>
                </select>
                <button
                  onClick={onBoostBoth}
                  disabled={
                    boostBusy
                    || !sourceUid
                    || !targetUid
                    || sourceUid === targetUid
                  }
                  title={
                    sourceBoosted && targetBoosted
                      ? "Both already boosted — this extends their expiry"
                      : `Boost both source and target to 1 Hz for ${boostDuration / 60} min`
                  }
                  className={
                    sourceBoosted || targetBoosted
                      ? "inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs text-amber-700 bg-amber-50 hover:bg-amber-100 disabled:opacity-50"
                      : "inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs text-slate-700 bg-white border border-slate-200 hover:bg-slate-50 disabled:opacity-50"
                  }
                >
                  <Zap className="w-3 h-3" />
                  Boost both
                  {sourceBoosted && targetBoosted
                    ? " (extend)"
                    : sourceBoosted || targetBoosted
                    ? " (the other)"
                    : ""}
                </button>
                <button
                  onClick={load}
                  disabled={loading}
                  title="Refresh"
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs bg-white border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <RefreshCw
                    className={`w-3 h-3 ${loading ? "animate-spin" : ""}`}
                  />
                  Refresh
                </button>
              </div>
            </div>
          </div>
        </div>
        {sourceUid && sourceUid === targetUid ? (
          <div className="text-xs text-amber-700">
            Source and target must be different.
          </div>
        ) : null}
        {error ? (
          <div className="px-3 py-2 bg-rose-50 text-rose-700 text-sm rounded">
            {error}
          </div>
        ) : null}
      </section>

      {data ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <Tile
              label="Points"
              value={String(data.summary.point_count)}
              subtitle={
                data.granularity === "raw"
                  ? `raw · ${data.bucket_s}s buckets`
                  : `${data.granularity} granularity`
              }
            />
            <Tile label="Sent" value={String(data.summary.sent_total)} />
            <Tile label="Lost" value={String(data.summary.lost_total)} />
            <Tile label="Loss %" value={formatPct(data.summary.loss_pct)} />
            <Tile
              label="Worst p95"
              value={formatMs(data.summary.rtt_p95_ms)}
            />
          </div>

          <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-slate-900">
                RTT — {hostnameOf(sourceUid)} → {hostnameOf(targetUid)}
              </h2>
              <div className="text-xs text-slate-500 flex items-center gap-3">
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block w-3 h-0.5 bg-sky-500" />
                  avg
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block w-3 h-0.5 bg-rose-500" />
                  p95
                </span>
                <span className="text-slate-400">tooltip: p50/p99 included</span>
              </div>
            </div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={chartData}
                  margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
                >
                  <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="tsLabel"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    minTickGap={28}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    label={{
                      value: "ms",
                      angle: -90,
                      position: "insideLeft",
                      style: { fill: "#64748b", fontSize: 11 },
                    }}
                  />
                  <Tooltip content={rttTooltip} />
                  <Line
                    type="monotone"
                    dataKey="rtt_avg"
                    name="avg"
                    stroke="#0ea5e9"
                    dot={false}
                    strokeWidth={2}
                    isAnimationActive={false}
                    connectNulls
                  />
                  <Line
                    type="monotone"
                    dataKey="rtt_p95"
                    name="p95"
                    stroke="#f43f5e"
                    dot={false}
                    strokeWidth={2}
                    strokeDasharray="4 2"
                    isAnimationActive={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
            <h2 className="text-sm font-medium text-slate-900">
              Loss % / Jitter
            </h2>
            <div className="text-xs text-slate-500 flex items-center gap-3">
              <span className="inline-flex items-center gap-1">
                <span className="inline-block w-3 h-0.5 bg-amber-500" />
                loss %
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block w-3 h-0.5 bg-violet-500" />
                jitter ms
              </span>
            </div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={chartData}
                  margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
                >
                  <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="tsLabel"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    minTickGap={28}
                  />
                  <YAxis
                    yAxisId="left"
                    tick={{ fontSize: 11, fill: "#f59e0b" }}
                    domain={[0, "auto"]}
                    label={{
                      value: "%",
                      angle: -90,
                      position: "insideLeft",
                      style: { fill: "#f59e0b", fontSize: 11 },
                    }}
                  />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    tick={{ fontSize: 11, fill: "#8b5cf6" }}
                    label={{
                      value: "ms",
                      angle: 90,
                      position: "insideRight",
                      style: { fill: "#8b5cf6", fontSize: 11 },
                    }}
                  />
                  <Tooltip
                    formatter={(v, name) =>
                      name === "loss"
                        ? formatPct(v == null ? null : Number(v))
                        : formatMs(v == null ? null : Number(v))
                    }
                  />
                  <Line
                    yAxisId="left"
                    type="monotone"
                    dataKey="loss"
                    name="loss"
                    stroke="#f59e0b"
                    dot={false}
                    strokeWidth={2}
                    isAnimationActive={false}
                    connectNulls
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="jitter"
                    name="jitter"
                    stroke="#8b5cf6"
                    dot={false}
                    strokeWidth={2}
                    isAnimationActive={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <WirelessPanel
            wireless={data.wireless}
            sourceUid={sourceUid}
            targetUid={targetUid}
            apResolver={apResolver}
            short={short}
          />

          {chartData.length === 0 ? (
            <div className="text-sm text-slate-500">
              No data for this pair in the selected range.
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function Tile({
  label,
  value,
  subtitle,
}: {
  label: string;
  value: string;
  subtitle?: string;
}) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="text-xl font-semibold text-slate-900">{value}</div>
      {subtitle ? (
        <div className="text-xs text-slate-500">{subtitle}</div>
      ) : null}
    </div>
  );
}

// Color palette for per-AP segments. Unknown BSSIDs use a neutral slate. When the
// same AP shows up across both agents we want the same color, so we key by BSSID.
const AP_PALETTE = [
  "#0ea5e9", // sky
  "#f59e0b", // amber
  "#10b981", // emerald
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#14b8a6", // teal
  "#f43f5e", // rose
  "#84cc16", // lime
  "#6366f1", // indigo
];
const UNKNOWN_COLOR = "#94a3b8";

/**
 * Per-agent wireless signal chart. Rendered only when at least one endpoint of
 * the pair has wireless samples in the window. The line visibly changes color
 * at roam boundaries (one Line component per (agent, AP) segment) so "which AP
 * am I on right now" is readable at a glance. Tooltip shows hostname, signal,
 * BSSID, and the resolved AP name when the BSSID is in the Access Points
 * table. Vertical dashed roam markers remain for the "moment of change".
 */
function WirelessPanel({
  wireless,
  sourceUid,
  targetUid,
  apResolver,
  short,
}: {
  wireless: WirelessTrendSeries[];
  sourceUid: string;
  targetUid: string;
  apResolver: ApResolver;
  short: boolean;
}) {
  if (!wireless || wireless.length === 0) return null;
  const relevant = wireless.filter(
    (w) => w.agent_uid === sourceUid || w.agent_uid === targetUid,
  );
  if (relevant.length === 0) return null;

  const hasAnySignal = relevant.some((w) =>
    w.points.some((p) => p.signal_dbm != null),
  );
  if (!hasAnySignal) return null;

  const apLabel = (bssid: string | null): string => {
    if (!bssid) return "unknown AP";
    return apResolver.name(bssid) ?? bssid;
  };

  // Group BSSIDs by physical AP (apResolver groupKey). Same physical AP →
  // same color regardless of which virtual BSSID the client is currently on.
  const groupColor: Map<string, string> = new Map();
  const groupBssid: Map<string, string | null> = new Map(); // representative bssid
  const groupApName: Map<string, string | undefined> = new Map();
  for (const w of relevant) {
    for (const p of w.points) {
      const gk = apResolver.groupKey(p.bssid);
      if (!groupColor.has(gk)) {
        groupColor.set(gk, AP_PALETTE[groupColor.size % AP_PALETTE.length]);
        groupBssid.set(gk, p.bssid ?? null);
        groupApName.set(gk, p.bssid ? apResolver.name(p.bssid) : undefined);
      }
    }
  }
  const colorForBssid = (bssid: string | null | undefined) => {
    const gk = apResolver.groupKey(bssid ?? null);
    return groupColor.get(gk) ?? UNKNOWN_COLOR;
  };

  // Wide-format rows keyed by the GROUP (not raw BSSID). Duplicate samples at
  // the roam boundary into both outgoing and incoming series so adjacent
  // colored line segments touch instead of breaking.
  interface Row {
    ts: number;
    tsLabel: string;
    [col: string]: number | string | null | undefined;
  }
  const rowByTs: Map<number, Row> = new Map();
  const seriesKeys: Set<string> = new Set();
  // ts-sorted samples per uid — tooltip uses these to surface each agent's
  // "current" value at the hover time, even when their samples are out of
  // phase with each other.
  const samplesByUid: Map<string, WirelessTrendPoint[]> = new Map();
  // ssids each uid was ever associated with during the window — surfaced if
  // it changed so the operator can tell at a glance.
  const ssidsByUid: Map<string, Set<string>> = new Map();

  for (const w of relevant) {
    const sortedPoints = w.points.slice().sort((a, b) => a.ts_ms - b.ts_ms);
    samplesByUid.set(w.agent_uid, sortedPoints);
    let prevGroup: string | null = null;
    for (const p of sortedPoints) {
      const curGroup = apResolver.groupKey(p.bssid ?? null);
      const curKey = `${w.agent_uid}::${curGroup}`;
      seriesKeys.add(curKey);
      if (p.ssid) {
        if (!ssidsByUid.has(w.agent_uid)) ssidsByUid.set(w.agent_uid, new Set());
        ssidsByUid.get(w.agent_uid)!.add(p.ssid);
      }

      const row = rowByTs.get(p.ts_ms) ?? { ts: p.ts_ms, tsLabel: "" };
      row[curKey] = p.signal_dbm;
      rowByTs.set(p.ts_ms, row);

      if (
        prevGroup !== null
        && curGroup !== prevGroup
        && prevGroup !== "__unknown__"
      ) {
        const outKey = `${w.agent_uid}::${prevGroup}`;
        seriesKeys.add(outKey);
        row[outKey] = p.signal_dbm;
      }
      prevGroup = curGroup;
    }
  }

  const rows: Row[] = Array.from(rowByTs.values())
    .sort((a, b) => a.ts - b.ts)
    .map((r) => ({ ...r, tsLabel: formatTs(r.ts, short) }));

  // Unique series list for rendering (keyed by agent + AP group).
  const series: Array<{ key: string; uid: string; groupKey: string }> = [];
  for (const key of seriesKeys) {
    const [uid, groupKey] = key.split("::");
    series.push({ key, uid, groupKey });
  }

  const hostnameOf = (uid: string) =>
    relevant.find((w) => w.agent_uid === uid)?.hostname ?? uid.slice(0, 8);
  const ifaceOf = (uid: string) =>
    relevant.find((w) => w.agent_uid === uid)?.iface_name;

  const tooltipContent = (props: {
    active?: boolean;
    label?: string | number;
    payload?: readonly unknown[];
  }) => {
    if (!props.active || !props.payload || !props.payload.length) return null;
    type Entry = {
      uid: string;
      value: number | null;
      bssid: string | null;
      ssid: string | null;
    };
    const first = props.payload[0] as { payload?: { ts?: number } } | undefined;
    const ts = first?.payload?.ts;
    if (ts == null) return null;

    // For each wireless agent in the pair, look up its latest sample with
    // ts_ms ≤ hover ts — that's "what the agent's signal was at this moment"
    // even if the actual sample fell on the OTHER agent's timestamp.
    const byUid: Map<string, Entry> = new Map();
    for (const w of relevant) {
      const samples = samplesByUid.get(w.agent_uid) ?? [];
      let found: WirelessTrendPoint | undefined;
      for (const s of samples) {
        if (s.ts_ms > Number(ts)) break;
        found = s;
      }
      if (found) {
        byUid.set(w.agent_uid, {
          uid: w.agent_uid,
          value: found.signal_dbm,
          bssid: found.bssid,
          ssid: found.ssid,
        });
      }
    }
    if (byUid.size === 0) return null;
    return (
      <div className="bg-white border border-slate-200 rounded shadow-lg text-xs p-2 space-y-1 min-w-[240px]">
        <div className="font-semibold text-slate-900">{formatTs(Number(ts), short)}</div>
        {Array.from(byUid.values()).map((e) => {
          const iface = ifaceOf(e.uid);
          const ssidCount = ssidsByUid.get(e.uid)?.size ?? 0;
          return (
            <div key={e.uid} className="space-y-0.5">
              <div className="flex items-center gap-1">
                <span
                  className="inline-block w-3 h-0.5"
                  style={{ background: colorForBssid(e.bssid) }}
                />
                <span className="font-medium text-slate-900">
                  {hostnameOf(e.uid)}
                </span>
                {iface ? (
                  <span className="text-slate-400 font-mono">({iface})</span>
                ) : null}
              </div>
              <div className="pl-4 text-slate-700">
                signal:{" "}
                <span className="font-mono">
                  {e.value == null ? "—" : `${e.value.toFixed(0)} dBm`}
                </span>
              </div>
              <div className="pl-4 text-slate-700">
                SSID:{" "}
                <span className="font-medium">{e.ssid ?? "—"}</span>
                {ssidCount > 1 ? (
                  <span
                    className="ml-1 text-amber-700"
                    title="SSID changed during this window"
                  >
                    (changed)
                  </span>
                ) : null}
              </div>
              <div className="pl-4 text-slate-700">
                AP:{" "}
                <span className="font-medium">
                  {apResolver.name(e.bssid) ?? "unknown"}
                </span>
              </div>
              {e.bssid ? (
                <div className="pl-4 text-slate-400 font-mono">{e.bssid}</div>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  };

  // Consolidated roam list under the chart (both agents). Filter out
  // "intra-AP" roams where the from/to BSSIDs resolve to the same physical
  // AP — those are radio/SSID hops on a Ruckus-style AP, not real roams.
  const roamList = relevant
    .flatMap((w) =>
      w.roams
        .filter(
          (r) =>
            apResolver.groupKey(r.from_bssid) !==
            apResolver.groupKey(r.to_bssid),
        )
        .map((r) => ({
          ts_ms: r.ts_ms,
          uid: w.agent_uid,
          from: r.from_bssid,
          to: r.to_bssid,
        })),
    )
    .sort((a, b) => a.ts_ms - b.ts_ms);

  // Legend — one entry per physical AP seen in the window. Format:
  //   "aa:bb:cc:dd:ee:ff · Attic AP" when mapped
  //   "aa:bb:cc:dd:ee:ff" alone when unmapped
  //   "unknown AP" when the agent never reported a BSSID
  const apLegend = Array.from(groupColor.entries()).map(([gk, color]) => {
    const bssid = groupBssid.get(gk) ?? null;
    const name = groupApName.get(gk);
    let label: string;
    if (!bssid) label = "unknown AP";
    else if (name) label = `${bssid} · ${name}`;
    else label = bssid;
    return { key: gk, color, label };
  });

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-medium text-slate-900">Wireless signal</h2>
        <div className="text-xs text-slate-500 flex items-center gap-3 flex-wrap">
          {apLegend.map((ap) => (
            <span key={ap.key} className="inline-flex items-center gap-1">
              <span
                className="inline-block w-3 h-0.5"
                style={{ background: ap.color }}
              />
              {ap.label}
            </span>
          ))}
        </div>
      </div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
            <XAxis
              dataKey="ts"
              type="number"
              domain={["dataMin", "dataMax"]}
              scale="time"
              tick={{ fontSize: 11, fill: "#64748b" }}
              tickFormatter={(v: number) => formatTs(v, short)}
              minTickGap={28}
            />
            <YAxis
              tick={{ fontSize: 11, fill: "#64748b" }}
              domain={["auto", "auto"]}
              label={{
                value: "dBm",
                angle: -90,
                position: "insideLeft",
                style: { fill: "#64748b", fontSize: 11 },
              }}
            />
            <Tooltip content={tooltipContent} />
            {series.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={groupColor.get(s.groupKey) ?? UNKNOWN_COLOR}
                dot={{ r: 2 }}
                strokeWidth={2}
                isAnimationActive={false}
                // Two agents polling out-of-phase means this series has null at
                // the other agent's timestamps. connectNulls=true draws a line
                // through those gaps within an AP segment. At a real roam,
                // we've already duplicated the sample into both the outgoing
                // and incoming series (above), so the OLD series terminates
                // cleanly at the roam ts anyway.
                connectNulls={true}
              />
            ))}
            {relevant.flatMap((w) =>
              w.roams
                .filter(
                  (r) =>
                    apResolver.groupKey(r.from_bssid) !==
                    apResolver.groupKey(r.to_bssid),
                )
                .map((roam) => (
                  <ReferenceLine
                    key={`${w.agent_uid}-${roam.ts_ms}`}
                    x={roam.ts_ms}
                    stroke={colorForBssid(roam.to_bssid)}
                    strokeDasharray="3 3"
                    label={{
                      value: `→ ${apLabel(roam.to_bssid)}`,
                      position: "top",
                      fill: colorForBssid(roam.to_bssid),
                      fontSize: 10,
                    }}
                  />
                )),
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {roamList.length > 0 ? (
        <div className="pt-1 text-xs text-slate-600">
          <div className="font-medium mb-1">Roam events</div>
          <ul className="space-y-0.5">
            {roamList.map((r, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className="font-mono text-slate-500">
                  {formatTs(r.ts_ms, true)}
                </span>
                <span className="text-slate-900">{hostnameOf(r.uid)}</span>
                <span className="text-slate-400">—</span>
                <span
                  className="inline-flex items-center gap-1"
                  style={{ color: colorForBssid(r.from) }}
                >
                  {apLabel(r.from)}
                </span>
                <span className="text-slate-400">→</span>
                <span
                  className="inline-flex items-center gap-1"
                  style={{ color: colorForBssid(r.to) }}
                >
                  {apLabel(r.to)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
