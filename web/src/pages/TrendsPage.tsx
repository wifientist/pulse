import { Plus, RefreshCw, X, Zap } from "lucide-react";
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

import { getAirspace, getTrends, startBoost } from "../api/endpoints";
import type {
  AirspaceResponse,
  TrendPoint,
  TrendResponse,
  WirelessTrendPoint,
  WirelessTrendSeries,
} from "../api/types";
import { useFilterStore } from "../store/filter";
import { useSnapshotStore } from "../store/snapshot";
import { type ApResolver, buildApResolver } from "../utils/apMatch";
import { bandLabel } from "../utils/time";

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

// Colors per pair slot. Chose three visually distinct hues that read well on
// white. Slot index stays stable so swapping pairs doesn't reshuffle colors.
const PAIR_PALETTE = ["#0ea5e9", "#f59e0b", "#8b5cf6"]; // sky, amber, violet
const MAX_PAIRS = 3;

interface Pair {
  source: string;
  target: string;
}

function pairKey(p: Pair): string {
  return `${p.source}__${p.target}`;
}

function isPairValid(p: Pair): boolean {
  return !!p.source && !!p.target && p.source !== p.target;
}

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

  // Mesh-eligible agents — the ones valid as source/target in a ping pair.
  // Monitor-role agents sit outside the mesh (no pings in or out), so showing
  // them in the pair dropdown would just produce empty trends.
  const meshAgents = useMemo(
    () =>
      activeAgents.filter(
        (a) => !a.interfaces.some((i) => i.role === "monitor"),
      ),
    [activeAgents],
  );

  const hostnameOf = useCallback(
    (uid: string) =>
      activeAgents.find((a) => a.agent_uid === uid)?.hostname ?? uid.slice(0, 8),
    [activeAgents],
  );

  const prefilledSource = filterSelected[0] ?? meshAgents[0]?.agent_uid ?? "";
  const prefilledTarget = filterSelected[1] ?? meshAgents[1]?.agent_uid ?? "";

  const [pairs, setPairs] = useState<Pair[]>([
    { source: prefilledSource, target: prefilledTarget },
  ]);

  // When prefill values become known (first snapshot), fill the first row if
  // still empty — don't stomp on user edits.
  useEffect(() => {
    setPairs((prev) => {
      const first = prev[0];
      if (!first || (first.source && first.target)) return prev;
      const next: Pair = {
        source: first.source || prefilledSource,
        target: first.target || prefilledTarget,
      };
      return [next, ...prev.slice(1)];
    });
  }, [prefilledSource, prefilledTarget]);

  const [range, setRange] = useState<RangeKey>("5m");
  const [dataByKey, setDataByKey] = useState<Record<string, TrendResponse>>({});
  const [airspaceByUid, setAirspaceByUid] = useState<
    Record<string, AirspaceResponse>
  >({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [boostDuration, setBoostDuration] = useState<number>(300);
  const [boostBusy, setBoostBusy] = useState<boolean>(false);

  // Monitor-role agents — scan samples come from these, one airspace
  // response per agent over the current range window.
  const monitorAgents = useMemo(
    () =>
      (snapshot?.agents ?? []).filter((a) =>
        a.interfaces.some((i) => i.role === "monitor"),
      ),
    [snapshot?.agents],
  );

  // Per-agent toggle for the airspace panel. Default: enabled for every
  // monitor agent as they come online, so the chart shows up on first load
  // without hunting for a toggle. Admin can uncheck to hide.
  const [airspaceEnabled, setAirspaceEnabled] = useState<Set<string>>(
    () => new Set(),
  );
  useEffect(() => {
    setAirspaceEnabled((prev) => {
      const next = new Set(prev);
      let changed = false;
      const currentUids = new Set(monitorAgents.map((a) => a.agent_uid));
      for (const a of monitorAgents) {
        if (!next.has(a.agent_uid)) {
          next.add(a.agent_uid);
          changed = true;
        }
      }
      // Prune entries for monitor agents that no longer exist (re-role, etc.).
      for (const uid of prev) {
        if (!currentUids.has(uid)) {
          next.delete(uid);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [monitorAgents]);
  const toggleAirspace = useCallback((uid: string) => {
    setAirspaceEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid);
      else next.add(uid);
      return next;
    });
  }, []);

  const boostedUids = useMemo(
    () =>
      new Set(
        (snapshot?.boosts ?? [])
          .filter((b) => b.expires_at > Date.now())
          .map((b) => b.agent_uid),
      ),
    [snapshot?.boosts],
  );

  const agentIdByUid = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of snapshot?.agents ?? []) m.set(a.agent_uid, a.id);
    return m;
  }, [snapshot?.agents]);

  const apResolver = useMemo(
    () => buildApResolver(snapshot?.access_points ?? []),
    [snapshot?.access_points],
  );

  const validPairs = useMemo(
    () => pairs.map((p, idx) => ({ p, idx })).filter(({ p }) => isPairValid(p)),
    [pairs],
  );

  const load = useCallback(async () => {
    if (validPairs.length === 0) {
      setDataByKey({});
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const ms = RANGE_OPTIONS.find((r) => r.key === range)?.ms ?? 5 * 60 * 1000;
      const now = Date.now();
      const results = await Promise.all(
        validPairs.map(({ p }) =>
          getTrends(p.source, p.target, now - ms, now).then((r) => ({
            key: pairKey(p),
            r,
          })),
        ),
      );
      const next: Record<string, TrendResponse> = {};
      for (const { key, r } of results) next[key] = r;
      setDataByKey(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [validPairs, range]);

  useEffect(() => {
    load();
  }, [load]);

  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    const windowMs =
      RANGE_OPTIONS.find((r) => r.key === range)?.ms ?? Infinity;
    if (windowMs > AUTO_REFRESH_MAX_RANGE_MS) return;
    const t = setInterval(() => loadRef.current(), AUTO_REFRESH_MS);
    return () => clearInterval(t);
  }, [range]);

  // Airspace — separate fetch per enabled monitor agent so one bad response
  // doesn't nuke the whole page. Refreshes on the same cadence as pair trends.
  const loadAirspace = useCallback(async () => {
    const toFetch = monitorAgents.filter((a) =>
      airspaceEnabled.has(a.agent_uid),
    );
    if (toFetch.length === 0) {
      setAirspaceByUid({});
      return;
    }
    const ms = RANGE_OPTIONS.find((r) => r.key === range)?.ms ?? 5 * 60 * 1000;
    const now = Date.now();
    const results = await Promise.all(
      toFetch.map((a) =>
        getAirspace(a.agent_uid, now - ms, now)
          .then((r) => ({ uid: a.agent_uid, r }))
          .catch(() => null),
      ),
    );
    const next: Record<string, AirspaceResponse> = {};
    for (const entry of results) {
      if (entry) next[entry.uid] = entry.r;
    }
    setAirspaceByUid(next);
  }, [monitorAgents, airspaceEnabled, range]);

  useEffect(() => {
    loadAirspace();
  }, [loadAirspace]);

  const loadAirspaceRef = useRef(loadAirspace);
  loadAirspaceRef.current = loadAirspace;
  useEffect(() => {
    const windowMs =
      RANGE_OPTIONS.find((r) => r.key === range)?.ms ?? Infinity;
    if (windowMs > AUTO_REFRESH_MAX_RANGE_MS) return;
    const t = setInterval(() => loadAirspaceRef.current(), AUTO_REFRESH_MS);
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

  // -----------------------------
  // Chart row merging
  // -----------------------------
  // Build a wide-format row set where each pair has its own columns:
  //   p${idx}_avg, p${idx}_p95, p${idx}_loss, p${idx}_jitter
  // Rows are the UNION of every pair's bucket timestamps; missing pair data
  // at a ts is null, and recharts connectNulls=true draws through the gaps.
  interface Row {
    ts: number;
    [col: string]: number | null;
  }
  const { rttRows, lossRows, totals, pairStats } = useMemo(() => {
    const byTs = new Map<number, Row>();
    let sentTotal = 0;
    let lostTotal = 0;
    let worstP95: number | null = null;
    const pairStatsLocal: Record<
      string,
      { sent: number; lost: number; p95_worst: number | null }
    > = {};
    for (const { p, idx } of validPairs) {
      const data = dataByKey[pairKey(p)];
      if (!data) continue;
      let sp = 0;
      let lp = 0;
      let worstPair: number | null = null;
      for (const pt of data.points as TrendPoint[]) {
        const row = byTs.get(pt.ts_ms) ?? ({ ts: pt.ts_ms } as Row);
        row[`p${idx}_avg`] = pt.rtt_avg_ms;
        row[`p${idx}_p95`] = pt.rtt_p95_ms;
        row[`p${idx}_loss`] = pt.loss_pct;
        row[`p${idx}_jitter`] = pt.jitter_ms;
        byTs.set(pt.ts_ms, row);
        sp += pt.sent;
        lp += pt.lost;
        if (pt.rtt_p95_ms != null) {
          worstPair = worstPair == null ? pt.rtt_p95_ms : Math.max(worstPair, pt.rtt_p95_ms);
        }
      }
      sentTotal += sp;
      lostTotal += lp;
      if (worstPair != null) {
        worstP95 = worstP95 == null ? worstPair : Math.max(worstP95, worstPair);
      }
      pairStatsLocal[pairKey(p)] = {
        sent: sp,
        lost: lp,
        p95_worst: worstPair,
      };
    }
    const rows = Array.from(byTs.values()).sort((a, b) => a.ts - b.ts);
    return {
      rttRows: rows,
      lossRows: rows,
      totals: {
        sent: sentTotal,
        lost: lostTotal,
        loss_pct: sentTotal > 0 ? (100 * lostTotal) / sentTotal : null,
        worst_p95: worstP95,
      },
      pairStats: pairStatsLocal,
    };
  }, [validPairs, dataByKey]);

  // granularity tile — "mixed" if the responses disagree across pairs.
  const granularityLabel = useMemo(() => {
    const gs = validPairs
      .map(({ p }) => dataByKey[pairKey(p)]?.granularity)
      .filter(Boolean);
    if (gs.length === 0) return "—";
    const set = new Set(gs);
    if (set.size > 1) return "mixed";
    return gs[0] ?? "—";
  }, [validPairs, dataByKey]);

  const rttTooltip = (props: {
    active?: boolean;
    label?: string | number;
    payload?: readonly unknown[];
  }) => {
    if (!props.active || !props.payload || !props.payload.length) return null;
    const first = props.payload[0] as { payload?: { ts?: number } } | undefined;
    const ts = first?.payload?.ts;
    if (ts == null) return null;

    const rows: Array<{
      idx: number;
      label: string;
      color: string;
      avg: number | null | undefined;
      p50: number | null | undefined;
      p95: number | null | undefined;
      p99: number | null | undefined;
      sent: number | undefined;
      lost: number | undefined;
    }> = [];
    for (const { p, idx } of validPairs) {
      const data = dataByKey[pairKey(p)];
      if (!data) continue;
      // Find nearest point with ts_ms <= hover ts for this pair.
      let found: TrendPoint | undefined;
      for (const pt of data.points) {
        if (pt.ts_ms > Number(ts)) break;
        found = pt;
      }
      if (!found) continue;
      rows.push({
        idx,
        label: `${hostnameOf(p.source)} → ${hostnameOf(p.target)}`,
        color: PAIR_PALETTE[idx % PAIR_PALETTE.length],
        avg: found.rtt_avg_ms,
        p50: found.rtt_p50_ms,
        p95: found.rtt_p95_ms,
        p99: found.rtt_p99_ms,
        sent: found.sent,
        lost: found.lost,
      });
    }
    if (rows.length === 0) return null;
    return (
      <div className="bg-white border border-slate-200 rounded shadow-lg text-xs p-2 space-y-1 min-w-[260px]">
        <div className="font-semibold text-slate-900">
          {formatTs(Number(ts), short)}
        </div>
        {rows.map((r) => (
          <div key={r.idx} className="space-y-0.5">
            <div className="flex items-center gap-1">
              <span
                className="inline-block w-3 h-0.5"
                style={{ background: r.color }}
              />
              <span className="font-medium text-slate-900">{r.label}</span>
            </div>
            <div className="pl-4 grid grid-cols-4 gap-x-2 text-slate-700">
              <span>
                avg:{" "}
                <span className="font-mono">{formatMs(r.avg)}</span>
              </span>
              <span>
                p50: <span className="font-mono">{formatMs(r.p50)}</span>
              </span>
              <span>
                p95: <span className="font-mono">{formatMs(r.p95)}</span>
              </span>
              <span>
                p99: <span className="font-mono">{formatMs(r.p99)}</span>
              </span>
            </div>
            <div className="pl-4 text-[10px] text-slate-400">
              sent {r.sent} · lost {r.lost}
            </div>
          </div>
        ))}
      </div>
    );
  };

  const lossJitterTooltip = (props: {
    active?: boolean;
    label?: string | number;
    payload?: readonly unknown[];
  }) => {
    if (!props.active || !props.payload || !props.payload.length) return null;
    const first = props.payload[0] as { payload?: { ts?: number } } | undefined;
    const ts = first?.payload?.ts;
    if (ts == null) return null;
    return (
      <div className="bg-white border border-slate-200 rounded shadow-lg text-xs p-2 space-y-1 min-w-[220px]">
        <div className="font-semibold text-slate-900">
          {formatTs(Number(ts), short)}
        </div>
        {validPairs.map(({ p, idx }) => {
          const data = dataByKey[pairKey(p)];
          if (!data) return null;
          let found: TrendPoint | undefined;
          for (const pt of data.points) {
            if (pt.ts_ms > Number(ts)) break;
            found = pt;
          }
          if (!found) return null;
          const color = PAIR_PALETTE[idx % PAIR_PALETTE.length];
          return (
            <div key={idx} className="space-y-0.5">
              <div className="flex items-center gap-1">
                <span
                  className="inline-block w-3 h-0.5"
                  style={{ background: color }}
                />
                <span className="font-medium text-slate-900">
                  {hostnameOf(p.source)} → {hostnameOf(p.target)}
                </span>
              </div>
              <div className="pl-4 text-slate-700">
                loss:{" "}
                <span className="font-mono">{formatPct(found.loss_pct)}</span>
                {"  "}jitter:{" "}
                <span className="font-mono">{formatMs(found.jitter_ms)}</span>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  // Wireless: merge series from all responses, dedupe by agent_uid (first
  // occurrence wins; same agent in two pairs is still one line).
  const wireless: WirelessTrendSeries[] = useMemo(() => {
    const seen = new Set<string>();
    const out: WirelessTrendSeries[] = [];
    for (const { p } of validPairs) {
      const data = dataByKey[pairKey(p)];
      if (!data?.wireless) continue;
      for (const w of data.wireless) {
        if (seen.has(w.agent_uid)) continue;
        seen.add(w.agent_uid);
        out.push(w);
      }
    }
    return out;
  }, [validPairs, dataByKey]);

  // -----------------------------
  // Pair editor helpers
  // -----------------------------
  const updatePair = (idx: number, patch: Partial<Pair>) =>
    setPairs((prev) =>
      prev.map((p, i) => (i === idx ? { ...p, ...patch } : p)),
    );
  const addPair = () =>
    setPairs((prev) =>
      prev.length >= MAX_PAIRS ? prev : [...prev, { source: "", target: "" }],
    );
  const removePair = (idx: number) =>
    setPairs((prev) =>
      prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx),
    );

  const uniqueInvolvedUids = useMemo(() => {
    const set = new Set<string>();
    for (const { p } of validPairs) {
      set.add(p.source);
      set.add(p.target);
    }
    return Array.from(set);
  }, [validPairs]);

  const someInvolvedBoosted = uniqueInvolvedUids.some((u) =>
    boostedUids.has(u),
  );

  const onBoostAll = async () => {
    const ids: number[] = [];
    for (const uid of uniqueInvolvedUids) {
      const id = agentIdByUid.get(uid);
      if (id != null) ids.push(id);
    }
    if (ids.length === 0) return;
    setBoostBusy(true);
    setError(null);
    try {
      await Promise.all(ids.map((id) => startBoost(id, boostDuration)));
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "boost failed");
    } finally {
      setBoostBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">Trends</h1>
        <p className="text-sm text-slate-500">
          Historical connectivity for one or more source→target pairs. Overlay
          up to {MAX_PAIRS} pairs to compare them side-by-side. Ranges ≤2h use
          raw samples (1 Hz during boost); 2h–24h uses minute aggregates;
          longer uses hour aggregates.
        </p>
      </div>

      <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-3">
        <div className="space-y-2">
          {pairs.map((p, idx) => {
            const color = PAIR_PALETTE[idx % PAIR_PALETTE.length];
            const sameErr = p.source && p.target && p.source === p.target;
            return (
              <div
                key={idx}
                className="grid grid-cols-[8px_1fr_8px_1fr_auto] gap-2 items-center text-sm"
              >
                <span
                  className="inline-block w-2 h-6 rounded-sm"
                  style={{ background: color }}
                  title={`Pair ${idx + 1}`}
                />
                <select
                  value={p.source}
                  onChange={(e) => updatePair(idx, { source: e.target.value })}
                  className="w-full border border-slate-200 rounded px-2 py-1"
                >
                  <option value="">— source —</option>
                  {meshAgents.map((a) => (
                    <option key={a.agent_uid} value={a.agent_uid}>
                      {a.hostname}
                    </option>
                  ))}
                </select>
                <span className="text-slate-400 text-center">→</span>
                <select
                  value={p.target}
                  onChange={(e) => updatePair(idx, { target: e.target.value })}
                  className="w-full border border-slate-200 rounded px-2 py-1"
                >
                  <option value="">— target —</option>
                  {meshAgents.map((a) => (
                    <option key={a.agent_uid} value={a.agent_uid}>
                      {a.hostname}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => removePair(idx)}
                  disabled={pairs.length <= 1}
                  title="Remove pair"
                  className="inline-flex items-center justify-center w-6 h-6 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 disabled:opacity-30"
                >
                  <X className="w-3 h-3" />
                </button>
                {sameErr ? (
                  <span className="col-span-5 text-xs text-amber-700 pl-5">
                    Source and target must be different for pair {idx + 1}.
                  </span>
                ) : null}
              </div>
            );
          })}
          {pairs.length < MAX_PAIRS ? (
            <button
              onClick={addPair}
              className="inline-flex items-center gap-1 text-xs text-sky-600 hover:text-sky-800"
            >
              <Plus className="w-3 h-3" /> Add pair
            </button>
          ) : null}
        </div>

        <div className="flex items-center gap-1 flex-wrap pt-1 border-t border-slate-100">
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
              onClick={onBoostAll}
              disabled={boostBusy || uniqueInvolvedUids.length === 0}
              title={`Boost all ${uniqueInvolvedUids.length} agent(s) involved to 1 Hz for ${boostDuration / 60} min`}
              className={
                someInvolvedBoosted
                  ? "inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs text-amber-700 bg-amber-50 hover:bg-amber-100 disabled:opacity-50"
                  : "inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs text-slate-700 bg-white border border-slate-200 hover:bg-slate-50 disabled:opacity-50"
              }
            >
              <Zap className="w-3 h-3" />
              Boost {uniqueInvolvedUids.length > 0 ? `all ${uniqueInvolvedUids.length}` : "all"}
              {someInvolvedBoosted ? " (extend)" : ""}
            </button>
            <button
              onClick={load}
              disabled={loading}
              title="Refresh"
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs bg-white border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {monitorAgents.length > 0 ? (
          <div className="flex items-center gap-3 flex-wrap pt-1 border-t border-slate-100 text-xs">
            <span className="text-slate-500 font-medium">Airspace:</span>
            {monitorAgents.map((a) => {
              const on = airspaceEnabled.has(a.agent_uid);
              return (
                <label
                  key={a.agent_uid}
                  className={
                    on
                      ? "inline-flex items-center gap-1.5 cursor-pointer text-violet-700"
                      : "inline-flex items-center gap-1.5 cursor-pointer text-slate-500"
                  }
                >
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggleAirspace(a.agent_uid)}
                    className="w-3.5 h-3.5 accent-violet-600"
                  />
                  {a.hostname}
                </label>
              );
            })}
            <span className="text-slate-400">
              — watch visible BSSIDs from monitor agents over this window
            </span>
          </div>
        ) : null}

        {error ? (
          <div className="px-3 py-2 bg-rose-50 text-rose-700 text-sm rounded">
            {error}
          </div>
        ) : null}
      </section>

      {validPairs.length === 0 ? (
        <div className="text-sm text-slate-500">
          Pick at least one valid pair above.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <Tile
              label="Pairs"
              value={String(validPairs.length)}
              subtitle={`${granularityLabel} granularity`}
            />
            <Tile label="Sent" value={String(totals.sent)} />
            <Tile label="Lost" value={String(totals.lost)} />
            <Tile label="Loss %" value={formatPct(totals.loss_pct)} />
            <Tile label="Worst p95" value={formatMs(totals.worst_p95)} />
          </div>

          <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <h2 className="text-sm font-medium text-slate-900">RTT</h2>
              <div className="text-xs text-slate-500 flex items-center gap-3 flex-wrap">
                {validPairs.map(({ p, idx }) => {
                  const c = PAIR_PALETTE[idx % PAIR_PALETTE.length];
                  return (
                    <span
                      key={idx}
                      className="inline-flex items-center gap-1"
                    >
                      <span
                        className="inline-block w-3 h-0.5"
                        style={{ background: c }}
                      />
                      {hostnameOf(p.source)} → {hostnameOf(p.target)}
                    </span>
                  );
                })}
                <span className="text-slate-400">
                  solid = avg · dashed = p95 · tooltip shows p50/p99 too
                </span>
              </div>
            </div>
            <p className="text-[11px] text-slate-500 -mt-1">
              <strong>avg</strong> = mean RTT of successful pings in the bucket.{" "}
              <strong>p50</strong> (median) — half the pings were faster than this.{" "}
              <strong>p95</strong> — only 5% were slower; a good "how bad does it
              usually get" signal.{" "}
              <strong>p99</strong> — only 1% were slower; worst-case for
              real traffic, where latency-sensitive apps feel pain.
            </p>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={rttRows}
                  margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
                >
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
                    label={{
                      value: "ms",
                      angle: -90,
                      position: "insideLeft",
                      style: { fill: "#64748b", fontSize: 11 },
                    }}
                  />
                  <Tooltip content={rttTooltip} />
                  {validPairs.map(({ idx }) => {
                    const c = PAIR_PALETTE[idx % PAIR_PALETTE.length];
                    return (
                      <Line
                        key={`avg-${idx}`}
                        type="monotone"
                        dataKey={`p${idx}_avg`}
                        stroke={c}
                        dot={false}
                        strokeWidth={2}
                        isAnimationActive={false}
                        connectNulls
                      />
                    );
                  })}
                  {validPairs.map(({ idx }) => {
                    const c = PAIR_PALETTE[idx % PAIR_PALETTE.length];
                    return (
                      <Line
                        key={`p95-${idx}`}
                        type="monotone"
                        dataKey={`p${idx}_p95`}
                        stroke={c}
                        dot={false}
                        strokeWidth={2}
                        strokeDasharray="4 2"
                        isAnimationActive={false}
                        connectNulls
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <h2 className="text-sm font-medium text-slate-900">
                Loss % / Jitter
              </h2>
              <div className="text-xs text-slate-500 flex items-center gap-3">
                <span>left axis: loss % · right axis: jitter ms</span>
              </div>
            </div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={lossRows}
                  margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
                >
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
                    yAxisId="left"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    domain={[0, "auto"]}
                    label={{
                      value: "%",
                      angle: -90,
                      position: "insideLeft",
                      style: { fill: "#64748b", fontSize: 11 },
                    }}
                  />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    label={{
                      value: "ms",
                      angle: 90,
                      position: "insideRight",
                      style: { fill: "#64748b", fontSize: 11 },
                    }}
                  />
                  <Tooltip content={lossJitterTooltip} />
                  {validPairs.map(({ idx }) => {
                    const c = PAIR_PALETTE[idx % PAIR_PALETTE.length];
                    return (
                      <Line
                        key={`loss-${idx}`}
                        yAxisId="left"
                        type="monotone"
                        dataKey={`p${idx}_loss`}
                        stroke={c}
                        dot={false}
                        strokeWidth={2}
                        isAnimationActive={false}
                        connectNulls
                      />
                    );
                  })}
                  {validPairs.map(({ idx }) => {
                    const c = PAIR_PALETTE[idx % PAIR_PALETTE.length];
                    return (
                      <Line
                        key={`jitter-${idx}`}
                        yAxisId="right"
                        type="monotone"
                        dataKey={`p${idx}_jitter`}
                        stroke={c}
                        dot={false}
                        strokeWidth={2}
                        strokeDasharray="2 3"
                        isAnimationActive={false}
                        connectNulls
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <WirelessPanel
            wireless={wireless}
            apResolver={apResolver}
            short={short}
            pairStatsUnused={pairStats}
          />
        </>
      )}

      {/* Airspace panels render independently of mesh pairs — a monitor agent
          can be the whole reason someone's on this page. */}
      {monitorAgents.map((a) => {
        if (!airspaceEnabled.has(a.agent_uid)) return null;
        const data = airspaceByUid[a.agent_uid];
        if (!data) return null;
        if (data.series.length === 0) {
          return (
            <section
              key={a.agent_uid}
              className="bg-white rounded-lg border border-slate-200 p-3"
            >
              <header className="flex items-center justify-between mb-1">
                <h2 className="text-sm font-medium text-slate-900">
                  Airspace —{" "}
                  <span className="text-slate-500">{a.hostname}</span>
                </h2>
              </header>
              <p className="text-xs text-slate-500">
                No scan samples yet in this window. Check that at least one
                SSID is in the Monitored SSIDs allowlist (Access Points page)
                and that the monitor agent sees it.
              </p>
            </section>
          );
        }
        return (
          <AirspacePanel
            key={a.agent_uid}
            hostname={a.hostname}
            data={data}
            short={short}
          />
        );
      })}
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

// -----------------------------
// Wireless panel
// -----------------------------
// Multiple wireless series (one per distinct agent). Colors key by AP group
// so BSSIDs on the same physical AP share a color. Vertical markers + roam
// list show transitions. Unchanged from single-pair behavior — the dedup
// logic above ensures we never render the same agent twice.

const AP_PALETTE = [
  "#0ea5e9",
  "#f59e0b",
  "#10b981",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f43f5e",
  "#84cc16",
  "#6366f1",
];
const UNKNOWN_COLOR = "#94a3b8";

// Wrapper: one chart per wireless client so each client's AP journey is its
// own visual story. Sharing a chart across clients mixed two different color
// paradigms (by-AP for wireless, by-pair everywhere else) and made it hard
// to tell "is this the same AP" from "is this the same client".
// Stable per-client color palette. Derived from agent_uid sort order so the
// same client keeps the same color across refreshes / pair changes.
const CLIENT_PALETTE = [
  "#0ea5e9", // sky
  "#f59e0b", // amber
  "#8b5cf6", // violet
  "#14b8a6", // teal
  "#f43f5e", // rose
  "#84cc16", // lime
];

function WirelessPanel({
  wireless,
  apResolver,
  short,
}: {
  wireless: WirelessTrendSeries[];
  apResolver: ApResolver;
  short: boolean;
  pairStatsUnused?: Record<string, { sent: number; lost: number; p95_worst: number | null }>;
}) {
  if (!wireless || wireless.length === 0) return null;
  const withSignal = wireless.filter((w) =>
    w.points.some((p) => p.signal_dbm != null),
  );
  if (withSignal.length === 0) return null;

  // Lock chart order: sort by hostname so refreshes don't shuffle the stack,
  // falling back to agent_uid for ties / missing hostnames.
  const sorted = [...withSignal].sort((a, b) => {
    const ah = a.hostname ?? a.agent_uid;
    const bh = b.hostname ?? b.agent_uid;
    return ah.localeCompare(bh);
  });
  // Same sort order drives CLIENT_PALETTE indices for the consolidated chart.
  const clientColor = new Map<string, string>();
  sorted.forEach((w, i) => {
    clientColor.set(w.agent_uid, CLIENT_PALETTE[i % CLIENT_PALETTE.length]);
  });

  return (
    <>
      <ConsolidatedWirelessChart
        wireless={sorted}
        clientColor={clientColor}
        apResolver={apResolver}
        short={short}
      />
      {sorted.map((w) => (
        <WirelessClientChart
          key={w.agent_uid}
          w={w}
          apResolver={apResolver}
          short={short}
        />
      ))}
    </>
  );
}

// SSID-specific dash patterns for the consolidated chart — lets multiple
// SSIDs on the same client share a base color but still be distinguishable.
const SSID_DASH_STYLES: (string | undefined)[] = [
  undefined,   // solid — primary SSID
  "6 3",       // dashed — second SSID
  "2 3",       // dotted
  "4 2 2 2",   // dash-dot
];

/**
 * All clients on one chart, fixed -90..-10 dBm y-axis so the shape of the
 * curves is comparable across runs. One line per (client, SSID) combo:
 * client drives the color, SSID drives the dash style (so a client that
 * switches SSIDs mid-window shows two lines that share a color).
 */
function ConsolidatedWirelessChart({
  wireless,
  clientColor,
  apResolver,
  short,
}: {
  wireless: WirelessTrendSeries[];
  clientColor: Map<string, string>;
  apResolver: ApResolver;
  short: boolean;
}) {
  // Consolidated freq lookup: BSSID → frequency_mhz, pulled from any series
  // that has a hit. Identical across series because frequency is a property
  // of the BSSID, not the reporting agent.
  const freqByBssid = new Map<string, number>();
  for (const w of wireless) {
    for (const [b, f] of Object.entries(w.bssid_frequencies ?? {})) {
      if (!freqByBssid.has(b)) freqByBssid.set(b, f);
    }
  }
  interface Row {
    ts: number;
    [col: string]: number | null | undefined;
  }

  const seriesMeta = new Map<
    string,
    { uid: string; ssid: string | null; dash: string | undefined }
  >();
  const ssidDashByUid = new Map<string, Map<string | null, string | undefined>>();
  for (const w of wireless) {
    // Preserve first-seen SSID order per client so the dash assignment is
    // stable across refreshes.
    const ssidsSeen: Array<string | null> = [];
    for (const p of w.points) {
      const s = p.ssid ?? null;
      if (!ssidsSeen.includes(s)) ssidsSeen.push(s);
    }
    const dashMap = new Map<string | null, string | undefined>();
    ssidsSeen.forEach((s, i) =>
      dashMap.set(s, SSID_DASH_STYLES[i % SSID_DASH_STYLES.length]),
    );
    ssidDashByUid.set(w.agent_uid, dashMap);
  }

  const rowByTs = new Map<number, Row>();
  const samplesByUid = new Map<string, WirelessTrendPoint[]>();
  const ssidsByUid = new Map<string, Set<string>>();
  for (const w of wireless) {
    const sorted = w.points.slice().sort((a, b) => a.ts_ms - b.ts_ms);
    samplesByUid.set(w.agent_uid, sorted);
    const dashMap = ssidDashByUid.get(w.agent_uid)!;
    for (const p of sorted) {
      const ssid = p.ssid ?? null;
      if (ssid) {
        if (!ssidsByUid.has(w.agent_uid))
          ssidsByUid.set(w.agent_uid, new Set());
        ssidsByUid.get(w.agent_uid)!.add(ssid);
      }
      const key = `${w.agent_uid}::${ssid ?? "__unknown__"}`;
      if (!seriesMeta.has(key)) {
        seriesMeta.set(key, {
          uid: w.agent_uid,
          ssid,
          dash: dashMap.get(ssid),
        });
      }
      const row = rowByTs.get(p.ts_ms) ?? { ts: p.ts_ms };
      row[key] = p.signal_dbm;
      rowByTs.set(p.ts_ms, row);
    }
  }

  const rows = Array.from(rowByTs.values()).sort((a, b) => a.ts - b.ts);
  const seriesKeys = Array.from(seriesMeta.keys());

  const hostnameOf = (uid: string) =>
    wireless.find((w) => w.agent_uid === uid)?.hostname ?? uid.slice(0, 8);
  const ifaceOf = (uid: string) =>
    wireless.find((w) => w.agent_uid === uid)?.iface_name;

  const tooltipContent = (props: {
    active?: boolean;
    payload?: readonly unknown[];
  }) => {
    if (!props.active || !props.payload || !props.payload.length) return null;
    const first = props.payload[0] as { payload?: { ts?: number } } | undefined;
    const ts = first?.payload?.ts;
    if (ts == null) return null;
    type Entry = {
      uid: string;
      value: number | null;
      ssid: string | null;
      bssid: string | null;
    };
    const byUid = new Map<string, Entry>();
    for (const w of wireless) {
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
          ssid: found.ssid,
          bssid: found.bssid,
        });
      }
    }
    if (byUid.size === 0) return null;
    return (
      <div className="bg-white border border-slate-200 rounded shadow-lg text-xs p-2 space-y-1 min-w-[240px]">
        <div className="font-semibold text-slate-900">
          {formatTs(Number(ts), short)}
        </div>
        {Array.from(byUid.values()).map((e) => {
          const iface = ifaceOf(e.uid);
          const ssidCount = ssidsByUid.get(e.uid)?.size ?? 0;
          const apName = e.bssid ? apResolver.name(e.bssid) : null;
          const band = bandLabel(e.bssid ? freqByBssid.get(e.bssid) : null);
          return (
            <div key={e.uid} className="space-y-0.5">
              <div className="flex items-center gap-1">
                <span
                  className="inline-block w-3 h-0.5"
                  style={{ background: clientColor.get(e.uid) ?? "#64748b" }}
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
                    title="This client has been on multiple SSIDs in this window"
                  >
                    ({ssidCount})
                  </span>
                ) : null}
              </div>
              <div className="pl-4 text-slate-700 flex items-center gap-1.5 flex-wrap">
                <span>AP:</span>
                <span className="font-medium">{apName ?? "unknown"}</span>
                {band ? (
                  <span
                    className={
                      band === "2.4 GHz"
                        ? "px-1 rounded bg-amber-50 text-amber-700 text-[9px] font-semibold"
                        : band === "5 GHz"
                          ? "px-1 rounded bg-sky-50 text-sky-700 text-[9px] font-semibold"
                          : "px-1 rounded bg-violet-50 text-violet-700 text-[9px] font-semibold"
                    }
                  >
                    {band}
                  </span>
                ) : null}
                {e.bssid ? (
                  <span className="text-slate-400 font-mono text-[10px]">
                    {e.bssid}
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-medium text-slate-900">
          All wireless clients · signal over time
        </h2>
        <div className="text-xs text-slate-500 flex items-center gap-3 flex-wrap">
          {wireless.map((w) => {
            const color = clientColor.get(w.agent_uid) ?? "#64748b";
            const ssids = ssidsByUid.get(w.agent_uid);
            const ssidText = ssids ? Array.from(ssids).join(" / ") : "—";
            return (
              <span
                key={w.agent_uid}
                className="inline-flex items-center gap-1"
              >
                <span
                  className="inline-block w-3 h-0.5"
                  style={{ background: color }}
                />
                <span className="font-medium text-slate-900">
                  {w.hostname ?? w.agent_uid.slice(0, 8)}
                </span>
                <span className="text-slate-400 font-mono">({ssidText})</span>
              </span>
            );
          })}
        </div>
      </div>
      <div className="h-64">
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
              // Fixed domain so the curve shapes stay comparable between runs
              // and you can watch attenuation sloping on a stable scale. The
              // server-side sanitizer drops readings above -10 dBm so the
              // axis can go all the way up without parse artifacts climbing
              // off the top.
              domain={[-90, -10]}
              ticks={[-10, -20, -30, -40, -50, -60, -70, -80, -90]}
              allowDataOverflow
              tick={{ fontSize: 11, fill: "#64748b" }}
              label={{
                value: "dBm",
                angle: -90,
                position: "insideLeft",
                style: { fill: "#64748b", fontSize: 11 },
              }}
            />
            <Tooltip content={tooltipContent} />
            {seriesKeys.map((k) => {
              const meta = seriesMeta.get(k)!;
              const color = clientColor.get(meta.uid) ?? "#64748b";
              return (
                <Line
                  key={k}
                  type="monotone"
                  dataKey={k}
                  stroke={color}
                  strokeDasharray={meta.dash}
                  dot={{ r: 2 }}
                  strokeWidth={2}
                  isAnimationActive={false}
                  connectNulls
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function WirelessClientChart({
  w,
  apResolver,
  short,
}: {
  w: WirelessTrendSeries;
  apResolver: ApResolver;
  short: boolean;
}) {
  const apLabel = (bssid: string | null): string => {
    if (!bssid) return "unknown AP";
    return apResolver.name(bssid) ?? bssid;
  };

  // Per-AP palette, keyed by groupKey (AP name when mapped, prefix-based key
  // otherwise) so Ruckus-style sibling BSSIDs share one color under one AP.
  const groupColor: Map<string, string> = new Map();
  const groupBssid: Map<string, string | null> = new Map();
  const groupApName: Map<string, string | undefined> = new Map();
  for (const p of w.points) {
    const gk = apResolver.groupKey(p.bssid);
    if (!groupColor.has(gk)) {
      groupColor.set(gk, AP_PALETTE[groupColor.size % AP_PALETTE.length]);
      groupBssid.set(gk, p.bssid ?? null);
      groupApName.set(gk, p.bssid ? apResolver.name(p.bssid) : undefined);
    }
  }
  const colorForBssid = (bssid: string | null | undefined) => {
    const gk = apResolver.groupKey(bssid ?? null);
    return groupColor.get(gk) ?? UNKNOWN_COLOR;
  };

  interface Row {
    ts: number;
    [col: string]: number | null | undefined;
  }
  const rowByTs: Map<number, Row> = new Map();
  const seriesKeys: Set<string> = new Set();
  const sortedPoints = w.points.slice().sort((a, b) => a.ts_ms - b.ts_ms);
  const ssidsSeen: Set<string> = new Set();
  let prevGroup: string | null = null;
  for (const p of sortedPoints) {
    const curGroup = apResolver.groupKey(p.bssid ?? null);
    const curKey = `ap:${curGroup}`;
    seriesKeys.add(curKey);
    if (p.ssid) ssidsSeen.add(p.ssid);
    const row = rowByTs.get(p.ts_ms) ?? { ts: p.ts_ms };
    row[curKey] = p.signal_dbm;
    rowByTs.set(p.ts_ms, row);
    // Touch the outgoing segment at the roam boundary so adjacent colored
    // segments meet visually instead of breaking.
    if (
      prevGroup !== null
      && curGroup !== prevGroup
      && prevGroup !== "__unknown__"
    ) {
      const outKey = `ap:${prevGroup}`;
      seriesKeys.add(outKey);
      row[outKey] = p.signal_dbm;
    }
    prevGroup = curGroup;
  }
  const rows: Row[] = Array.from(rowByTs.values()).sort((a, b) => a.ts - b.ts);
  const series: Array<{ key: string; groupKey: string }> = Array.from(
    seriesKeys,
  ).map((key) => ({ key, groupKey: key.slice(3) })); // strip "ap:" prefix

  // SSID change events. Unlike roams (BSSID / physical AP change), these are
  // the SSID associations changing mid-window — usually a sign of config
  // shuffling on the infrastructure side or the client reassociating to a
  // different network entirely.
  const ssidEvents: { ts_ms: number; from: string | null; to: string | null }[] = [];
  {
    let prev: string | null | undefined = undefined;
    for (const p of sortedPoints) {
      if (prev === undefined) {
        prev = p.ssid ?? null;
        continue;
      }
      const cur = p.ssid ?? null;
      if (cur !== prev) {
        ssidEvents.push({ ts_ms: p.ts_ms, from: prev, to: cur });
        prev = cur;
      }
    }
  }
  // "Primary" SSID = most recent one actually observed (falls back to most
  // recent non-null). Shown in the section header so the current association
  // is readable at a glance.
  const latestSsid =
    [...sortedPoints].reverse().find((p) => p.ssid)?.ssid ?? null;
  const ssidChanged = ssidsSeen.size > 1;

  const tooltipContent = (props: {
    active?: boolean;
    payload?: readonly unknown[];
  }) => {
    if (!props.active || !props.payload || !props.payload.length) return null;
    const first = props.payload[0] as { payload?: { ts?: number } } | undefined;
    const ts = first?.payload?.ts;
    if (ts == null) return null;
    // Latest sample with ts_ms ≤ hover ts — so the tooltip tracks the line
    // even when the cursor lands between samples.
    let found: WirelessTrendPoint | undefined;
    for (const p of sortedPoints) {
      if (p.ts_ms > Number(ts)) break;
      found = p;
    }
    if (!found) return null;
    return (
      <div className="bg-white border border-slate-200 rounded shadow-lg text-xs p-2 space-y-0.5 min-w-[220px]">
        <div className="font-semibold text-slate-900">
          {formatTs(Number(ts), short)}
        </div>
        <div className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-0.5"
            style={{ background: colorForBssid(found.bssid) }}
          />
          <span className="font-medium text-slate-900">
            {w.hostname ?? w.agent_uid.slice(0, 8)}
          </span>
          {w.iface_name ? (
            <span className="text-slate-400 font-mono">({w.iface_name})</span>
          ) : null}
        </div>
        <div className="pl-4 text-slate-700">
          signal:{" "}
          <span className="font-mono">
            {found.signal_dbm == null
              ? "—"
              : `${found.signal_dbm.toFixed(0)} dBm`}
          </span>
        </div>
        <div className="pl-4 text-slate-700">
          SSID:{" "}
          <span className="font-medium">{found.ssid ?? "—"}</span>
          {ssidsSeen.size > 1 ? (
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
            {apResolver.name(found.bssid) ?? "unknown"}
          </span>
        </div>
        {found.bssid ? (
          <div className="pl-4 text-slate-400 font-mono">{found.bssid}</div>
        ) : null}
      </div>
    );
  };

  const roamList = w.roams
    .filter(
      (r) =>
        apResolver.groupKey(r.from_bssid) !==
        apResolver.groupKey(r.to_bssid),
    )
    .sort((a, b) => a.ts_ms - b.ts_ms);

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
        <h2 className="text-sm font-medium text-slate-900">
          Wireless —{" "}
          <span className="font-normal">
            {w.hostname ?? w.agent_uid.slice(0, 8)}
          </span>
          {w.iface_name ? (
            <span className="ml-1 text-slate-400 font-mono text-xs">
              ({w.iface_name})
            </span>
          ) : null}
          <span className="ml-2 text-slate-500 text-xs">
            SSID:{" "}
            <span className="font-medium text-slate-700">
              {latestSsid ?? "—"}
            </span>
            {ssidChanged ? (
              <span
                className="ml-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-semibold"
                title="SSID changed during this window — see events below"
              >
                changed {ssidsSeen.size}×
              </span>
            ) : null}
          </span>
        </h2>
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
              // Fixed domain (not auto) so a single bad iw sample can't drag
              // the axis; the server-side sanitizer already drops anything
              // above -10 dBm, and `allowDataOverflow` crops what slips.
              tick={{ fontSize: 11, fill: "#64748b" }}
              domain={[-90, -10]}
              ticks={[-10, -20, -30, -40, -50, -60, -70, -80, -90]}
              allowDataOverflow
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
                connectNulls
              />
            ))}
            {roamList.map((roam) => (
              <ReferenceLine
                key={roam.ts_ms}
                x={roam.ts_ms}
                stroke={colorForBssid(roam.to_bssid)}
                strokeDasharray="3 3"
                label={{
                  value: `→ ${apLabel(roam.to_bssid)}`,
                  // insideTop keeps the label within the chart area so it
                  // doesn't clip off the top of the panel.
                  position: "insideTop",
                  fill: colorForBssid(roam.to_bssid),
                  fontSize: 10,
                }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {roamList.length > 0 || ssidEvents.length > 0 ? (
        <div className="pt-1 text-xs text-slate-600">
          <div className="font-medium mb-1">Events</div>
          <ul className="space-y-0.5">
            {[
              ...roamList.map((r) => ({
                kind: "roam" as const,
                ts_ms: r.ts_ms,
                from: r.from_bssid,
                to: r.to_bssid,
              })),
              ...ssidEvents.map((e) => ({
                kind: "ssid" as const,
                ts_ms: e.ts_ms,
                from: e.from,
                to: e.to,
              })),
            ]
              .sort((a, b) => a.ts_ms - b.ts_ms)
              .map((ev, i) => (
                <li key={i} className="flex items-center gap-2">
                  <span className="font-mono text-slate-500 w-14 shrink-0">
                    {formatTs(ev.ts_ms, true)}
                  </span>
                  {ev.kind === "roam" ? (
                    <>
                      <span className="text-slate-400 w-10 shrink-0 uppercase text-[10px]">
                        roam
                      </span>
                      <span
                        className="inline-flex items-center gap-1"
                        style={{ color: colorForBssid(ev.from) }}
                      >
                        {apLabel(ev.from)}
                      </span>
                      <span className="text-slate-400">→</span>
                      <span
                        className="inline-flex items-center gap-1"
                        style={{ color: colorForBssid(ev.to) }}
                      >
                        {apLabel(ev.to)}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="text-amber-700 w-10 shrink-0 uppercase text-[10px] font-semibold">
                        ssid
                      </span>
                      <span className="text-slate-700">
                        {ev.from ?? "—"}
                      </span>
                      <span className="text-slate-400">→</span>
                      <span className="text-slate-900 font-medium">
                        {ev.to ?? "—"}
                      </span>
                    </>
                  )}
                </li>
              ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}


// -----------------------------
// Airspace panel
// -----------------------------
// One chart per monitor agent. X-axis: time over the page's range. Y-axis:
// fixed −90..−10 dBm for visual stability as new BSSIDs appear/disappear.
// Line per BSSID, colored by its mapped AP (shared palette with WirelessPanel)
// or gray/dashed if unmapped. SSID is used as a tie-breaker in the legend.

const AIRSPACE_PALETTE = [
  "#0ea5e9",
  "#f59e0b",
  "#10b981",
  "#8b5cf6",
  "#ef4444",
  "#6366f1",
  "#ec4899",
  "#14b8a6",
];

function AirspacePanel({
  hostname,
  data,
  short,
}: {
  hostname: string | null;
  data: AirspaceResponse;
  short: boolean;
}) {
  // Stable color per series: mapped APs keyed by ap_name (so the same AP is
  // the same color across pages), unmapped BSSIDs get a palette slot by hash.
  const colorFor = useCallback((s: { ap_name: string | null; bssid: string }) => {
    const key = s.ap_name ?? s.bssid;
    let h = 0;
    for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
    return AIRSPACE_PALETTE[Math.abs(h) % AIRSPACE_PALETTE.length];
  }, []);

  // Band filter chips. A series with unknown frequency maps to "unknown";
  // we always include it in the default-enabled set so rare parse gaps
  // don't hide lines.
  const [enabledBands, setEnabledBands] = useState<Set<string>>(
    () => new Set(["2.4 GHz", "5 GHz", "6 GHz", "unknown"]),
  );
  const toggleBand = (band: string) =>
    setEnabledBands((prev) => {
      const next = new Set(prev);
      if (next.has(band)) next.delete(band);
      else next.add(band);
      return next;
    });
  // Which bands actually appear in this window — only offer chips for those,
  // so the control doesn't show "6 GHz" on a 5-only scan.
  const bandsPresent = useMemo(() => {
    const s = new Set<string>();
    for (const ser of data.series) {
      s.add(bandLabel(ser.frequency_mhz) ?? "unknown");
    }
    return s;
  }, [data.series]);

  const visibleSeries = useMemo(
    () =>
      data.series.filter((s) =>
        enabledBands.has(bandLabel(s.frequency_mhz) ?? "unknown"),
      ),
    [data.series, enabledBands],
  );

  // Wide rows: one key per BSSID, null-padded on ts misses. recharts with
  // connectNulls=true draws through scan gaps.
  const rows = useMemo(() => {
    const byTs = new Map<number, Record<string, number | null>>();
    for (const s of visibleSeries) {
      for (const p of s.points) {
        const r = byTs.get(p.ts_ms) ?? {};
        r[s.bssid] = p.signal_dbm;
        byTs.set(p.ts_ms, r);
      }
    }
    return Array.from(byTs.entries())
      .sort(([a], [b]) => a - b)
      .map(([ts, cols]) => ({ ts, ...cols }));
  }, [visibleSeries]);

  const label = hostname ?? data.agent_uid.slice(0, 8);

  const bandChipClass = (band: string, on: boolean): string => {
    if (!on) return "px-2 py-0.5 rounded border border-slate-200 bg-white text-slate-400 text-[10px] font-semibold cursor-pointer hover:bg-slate-50";
    if (band === "2.4 GHz")
      return "px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-semibold cursor-pointer";
    if (band === "5 GHz")
      return "px-2 py-0.5 rounded bg-sky-100 text-sky-800 text-[10px] font-semibold cursor-pointer";
    if (band === "6 GHz")
      return "px-2 py-0.5 rounded bg-violet-100 text-violet-800 text-[10px] font-semibold cursor-pointer";
    return "px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-[10px] font-semibold cursor-pointer";
  };

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-3">
      <header className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <h2 className="text-sm font-medium text-slate-900">
          Airspace — <span className="text-slate-500">{label}</span>
        </h2>
        <div className="flex items-center gap-2 text-xs">
          {["2.4 GHz", "5 GHz", "6 GHz", "unknown"]
            .filter((b) => bandsPresent.has(b))
            .map((b) => {
              const on = enabledBands.has(b);
              return (
                <button
                  key={b}
                  onClick={() => toggleBand(b)}
                  className={bandChipClass(b, on)}
                  title={`Toggle ${b}`}
                >
                  {b}
                </button>
              );
            })}
          <span className="text-slate-500">
            {visibleSeries.length}/{data.series.length} BSSID
            {data.series.length === 1 ? "" : "s"}
          </span>
        </div>
      </header>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis
              dataKey="ts"
              tickFormatter={(v: number) => formatTs(v, short)}
              stroke="#94a3b8"
              fontSize={11}
            />
            <YAxis
              domain={[-90, -10]}
              ticks={[-10, -20, -30, -40, -50, -60, -70, -80, -90]}
              allowDataOverflow
              stroke="#94a3b8"
              fontSize={11}
              label={{
                value: "dBm",
                angle: -90,
                position: "insideLeft",
                style: { fill: "#94a3b8", fontSize: 11 },
              }}
            />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 6 }}
              labelFormatter={(v) => formatTs(v as number, short)}
              formatter={(value, name) => {
                const s = data.series.find((x) => x.bssid === name);
                const band = bandLabel(s?.frequency_mhz);
                const bandSuffix = band ? ` · ${band}` : "";
                const label = s?.ap_name
                  ? `${s.ap_name}${s.ssid ? ` (${s.ssid})` : ""}${bandSuffix}`
                  : `${s?.ssid ?? "?"} ${name}${bandSuffix}`;
                return [`${value} dBm`, label];
              }}
            />
            {visibleSeries.map((s) => (
              <Line
                key={s.bssid}
                type="monotone"
                dataKey={s.bssid}
                stroke={colorFor(s)}
                strokeWidth={s.ap_name ? 2 : 1.25}
                strokeDasharray={s.ap_name ? undefined : "3 3"}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {visibleSeries.map((s) => {
          const band = bandLabel(s.frequency_mhz);
          return (
            <span key={s.bssid} className="inline-flex items-center gap-1.5">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ backgroundColor: colorFor(s) }}
              />
              <span className="text-slate-700">
                {s.ap_name ?? s.ssid ?? s.bssid}
              </span>
              {s.ap_name && s.ssid ? (
                <span className="text-slate-400">({s.ssid})</span>
              ) : null}
              {band ? (
                <span
                  className={
                    band === "2.4 GHz"
                      ? "px-1 rounded bg-amber-50 text-amber-700 text-[9px] font-semibold"
                      : band === "5 GHz"
                        ? "px-1 rounded bg-sky-50 text-sky-700 text-[9px] font-semibold"
                        : "px-1 rounded bg-violet-50 text-violet-700 text-[9px] font-semibold"
                  }
                >
                  {band}
                </span>
              ) : null}
              <span className="text-slate-400 font-mono text-[10px]">{s.bssid}</span>
            </span>
          );
        })}
      </div>
    </section>
  );
}
