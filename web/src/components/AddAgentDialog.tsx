import { Check, Copy, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { mintEnrollmentToken } from "../api/endpoints";
import type { NewEnrollmentTokenResponse } from "../api/types";

interface Props {
  open: boolean;
  onClose: () => void;
}

type Phase =
  | { kind: "form"; error: string | null; submitting: boolean }
  | { kind: "success"; token: NewEnrollmentTokenResponse };

export default function AddAgentDialog({ open, onClose }: Props) {
  const [phase, setPhase] = useState<Phase>({
    kind: "form",
    error: null,
    submitting: false,
  });
  const [label, setLabel] = useState("");
  const [usesRemaining, setUsesRemaining] = useState("1");
  const [expiresHours, setExpiresHours] = useState("");

  // Reset state every time the dialog reopens.
  useEffect(() => {
    if (open) {
      setPhase({ kind: "form", error: null, submitting: false });
      setLabel("");
      setUsesRemaining("1");
      setExpiresHours("");
    }
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!label.trim()) return;

    const uses = usesRemaining.trim() ? Number(usesRemaining) : null;
    if (uses !== null && (!Number.isFinite(uses) || uses < 1)) {
      setPhase({ kind: "form", error: "uses must be ≥ 1 or empty", submitting: false });
      return;
    }
    const hours = expiresHours.trim() ? Number(expiresHours) : null;
    if (hours !== null && (!Number.isFinite(hours) || hours <= 0)) {
      setPhase({ kind: "form", error: "expires (hours) must be > 0 or empty", submitting: false });
      return;
    }

    setPhase({ kind: "form", error: null, submitting: true });
    try {
      const token = await mintEnrollmentToken({
        label: label.trim(),
        uses_remaining: uses,
        expires_at: hours ? Date.now() + hours * 3_600_000 : null,
      });
      setPhase({ kind: "success", token });
    } catch (err) {
      setPhase({
        kind: "form",
        error: err instanceof Error ? err.message : "mint failed",
        submitting: false,
      });
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-xl bg-white rounded-lg shadow-xl border border-slate-200">
        <header className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
          <h2 className="text-sm font-semibold text-slate-900">Add agent</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-900"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </header>

        {phase.kind === "form" ? (
          <FormPhase
            label={label}
            setLabel={setLabel}
            usesRemaining={usesRemaining}
            setUsesRemaining={setUsesRemaining}
            expiresHours={expiresHours}
            setExpiresHours={setExpiresHours}
            onSubmit={onSubmit}
            error={phase.error}
            submitting={phase.submitting}
            onClose={onClose}
          />
        ) : (
          <SuccessPhase token={phase.token} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

function FormPhase(props: {
  label: string;
  setLabel: (s: string) => void;
  usesRemaining: string;
  setUsesRemaining: (s: string) => void;
  expiresHours: string;
  setExpiresHours: (s: string) => void;
  onSubmit: (e: FormEvent) => void;
  error: string | null;
  submitting: boolean;
  onClose: () => void;
}) {
  return (
    <form onSubmit={props.onSubmit} className="p-4 space-y-4">
      <p className="text-sm text-slate-600">
        Mints a pre-shared enrollment token. You'll get a one-time copy of the plaintext
        plus an install command to paste on the new device. The agent will show up under
        "Pending enrollments" for approval once it runs.
      </p>
      <div className="space-y-3">
        <label className="block">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            Label
          </span>
          <input
            type="text"
            value={props.label}
            onChange={(e) => props.setLabel(e.target.value)}
            placeholder="e.g. fleet-batch-2, lxc-LAN4, one-off"
            autoFocus
            className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Uses remaining
            </span>
            <input
              type="number"
              min={1}
              value={props.usesRemaining}
              onChange={(e) => props.setUsesRemaining(e.target.value)}
              placeholder="blank = unlimited"
              className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
            />
          </label>
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Expires in (hours)
            </span>
            <input
              type="number"
              min={0}
              step="0.5"
              value={props.expiresHours}
              onChange={(e) => props.setExpiresHours(e.target.value)}
              placeholder="blank = never"
              className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
            />
          </label>
        </div>
      </div>

      {props.error ? (
        <div className="px-3 py-2 bg-rose-50 text-rose-700 text-sm rounded">
          {props.error}
        </div>
      ) : null}

      <div className="flex justify-end gap-2 pt-2 border-t border-slate-200">
        <button
          type="button"
          onClick={props.onClose}
          className="px-3 py-1.5 rounded bg-slate-100 text-slate-700 hover:bg-slate-200 text-sm"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={props.submitting || !props.label.trim()}
          className="px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 disabled:opacity-50 text-sm"
        >
          {props.submitting ? "Minting…" : "Mint token"}
        </button>
      </div>
    </form>
  );
}

function SuccessPhase({
  token,
  onClose,
}: {
  token: NewEnrollmentTokenResponse;
  onClose: () => void;
}) {
  const serverUrl =
    typeof window !== "undefined" ? window.location.origin : "http://<server>:8080";

  const installCmd = useMemo(
    () =>
      [
        `# on the new device — from /tmp where you've scp'd install-agent.sh + pulse-source.tar.gz:`,
        `sudo PULSE_SERVER_URL=${serverUrl} \\`,
        `     PULSE_ENROLLMENT_TOKEN='${token.plaintext}' \\`,
        `     PULSE_REPORTED_IP=<this-device's-10.20.x.x-test-ip> \\`,
        `     PULSE_SOURCE_TAR=/tmp/pulse-source.tar.gz \\`,
        `     /tmp/install-agent.sh`,
      ].join("\n"),
    [token.plaintext, serverUrl],
  );

  return (
    <div className="p-4 space-y-4">
      <div className="px-3 py-2 rounded bg-amber-50 border border-amber-200 text-amber-900 text-xs">
        This is the only time you'll see the plaintext token. Copy it now.
      </div>

      <section>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            Enrollment token (plaintext)
          </span>
          <CopyButton text={token.plaintext} />
        </div>
        <pre className="bg-slate-900 text-emerald-300 font-mono text-xs p-3 rounded overflow-x-auto whitespace-pre-wrap break-all">
          {token.plaintext}
        </pre>
      </section>

      <section>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            Install command
          </span>
          <CopyButton text={installCmd} />
        </div>
        <pre className="bg-slate-900 text-slate-100 font-mono text-xs p-3 rounded overflow-x-auto">
          {installCmd}
        </pre>
        <p className="text-xs text-slate-500 mt-2">
          You'll need to scp{" "}
          <code className="font-mono">install-agent.sh</code> and{" "}
          <code className="font-mono">pulse-source.tar.gz</code> to{" "}
          <code className="font-mono">/tmp</code> on the target device first. Once the
          agent starts, it'll show up under "Pending enrollments" for you to approve.
        </p>
      </section>

      <div className="flex justify-end pt-2 border-t border-slate-200">
        <button
          type="button"
          onClick={onClose}
          className="px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm"
        >
          Done
        </button>
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — user can still select manually */
    }
  };

  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900"
    >
      {copied ? (
        <>
          <Check className="w-3 h-3" /> copied
        </>
      ) : (
        <>
          <Copy className="w-3 h-3" /> copy
        </>
      )}
    </button>
  );
}
