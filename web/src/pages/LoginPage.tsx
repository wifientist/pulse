import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Activity } from "lucide-react";

import { useAuthStore } from "../store/auth";

export default function LoginPage() {
  const setToken = useAuthStore((s) => s.setToken);
  const navigate = useNavigate();
  const [value, setValue] = useState("");

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    setToken(trimmed);
    navigate("/", { replace: true });
  };

  return (
    <div className="min-h-screen grid place-items-center p-6">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md bg-white rounded-lg shadow border border-slate-200 p-6 space-y-4"
      >
        <div className="flex items-center gap-2 text-slate-900">
          <Activity className="w-6 h-6 text-sky-500" />
          <h1 className="text-xl font-semibold">Pulse</h1>
        </div>
        <p className="text-sm text-slate-600">
          Paste your admin bearer token to sign in.
        </p>
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="pulse admin token"
          rows={3}
          autoFocus
          className="w-full font-mono text-sm rounded border border-slate-300 bg-white text-slate-900 placeholder:text-slate-400 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-sky-400"
        />
        <button
          type="submit"
          className="w-full rounded bg-sky-500 hover:bg-sky-600 disabled:bg-slate-300 text-white font-medium py-2"
          disabled={!value.trim()}
        >
          Sign in
        </button>
      </form>
    </div>
  );
}
