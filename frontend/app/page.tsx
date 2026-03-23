"use client";

import { useState, useRef, useCallback, FormEvent } from "react";
import ReactMarkdown from "react-markdown";

interface ProgressStep {
  message: string;
  type?: string;
  function_name?: string;
  outcome?: string;
  current?: number;
  total?: number;
  timestamp?: string;
}

interface SearchResult {
  query: string;
  website: string;
  answer: string;
  citations: Array<{ url: string; title: string }>;
}

type SearchState = "idle" | "submitting" | "streaming" | "complete" | "error";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [website, setWebsite] = useState("");
  const [state, setState] = useState<SearchState>("idle");
  const [steps, setSteps] = useState<ProgressStep[]>([]);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const eventSourceRef = useRef<EventSource | null>(null);

  const hasStarted = state !== "idle";

  const handleSearch = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!query.trim() || !website.trim()) return;

      // Reset
      setSteps([]);
      setResult(null);
      setError("");
      setState("submitting");

      // Close any previous EventSource
      eventSourceRef.current?.close();

      try {
        // 1. Invoke the Tensorlake search
        const invokeRes = await fetch("/api/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: query.trim(), website: website.trim() }),
        });

        if (!invokeRes.ok) {
          const body = await invokeRes.json().catch(() => ({}));
          throw new Error(body.error || `Request failed (${invokeRes.status})`);
        }

        const { request_id } = await invokeRes.json();
        setState("streaming");

        // 2. Open native EventSource for SSE progress
        const es = new EventSource(
          `/api/search/stream?requestId=${request_id}`
        );
        eventSourceRef.current = es;

        es.addEventListener("progress", (evt) => {
          const parsed = JSON.parse(evt.data);
          setSteps((prev) => {
            if (
              prev.length > 0 &&
              prev[prev.length - 1].message === parsed.message
            ) {
              return prev;
            }
            return [...prev, parsed];
          });
        });

        es.addEventListener("result", (evt) => {
          const parsed = JSON.parse(evt.data);
          setResult(parsed);
          setState("complete");
          es.close();
        });

        es.addEventListener("done", () => {
          setState("complete");
          es.close();
        });

        es.addEventListener("error", () => {
          // EventSource fires generic error on connection loss
          // Only treat as error if we haven't got a result
          setResult((currentResult) => {
            if (!currentResult) {
              setError("Connection to search stream lost");
              setState("error");
            }
            return currentResult;
          });
          es.close();
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong");
        setState("error");
      }
    },
    [query, website]
  );

  const handleTryExample = () => {
    setQuery("Explain Quantum Computing");
    setWebsite("https://en.wikipedia.org");
  };

  const handleNewSearch = () => {
    eventSourceRef.current?.close();
    setState("idle");
    setQuery("");
    setWebsite("");
    setSteps([]);
    setResult(null);
    setError("");
  };

  return (
    <div className="min-h-screen bg-white flex flex-col items-center px-4 pb-16">
      {/* Header */}
      <nav className="w-full max-w-3xl py-6 flex items-center justify-between">
        <button onClick={handleNewSearch} className="cursor-pointer">
          <h1 className="text-xl font-bold tracking-tighter text-slate-900">
            AGENTIC SEARCH
          </h1>
        </button>
        {hasStarted && (
          <button
            onClick={handleNewSearch}
            className="text-sm text-slate-500 hover:text-slate-700 transition-colors cursor-pointer"
          >
            New search
          </button>
        )}
      </nav>

      {/* Search Container */}
      <div
        className={`w-full max-w-2xl transition-all duration-700 ease-in-out ${
          hasStarted ? "mt-0" : "mt-[22vh]"
        }`}
      >
        <form onSubmit={handleSearch}>
          {/* Main query input */}
          <div className="relative">
            <input
              type="text"
              className="w-full py-4 pl-12 pr-16 text-lg border border-slate-200 rounded-2xl shadow-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all placeholder:text-slate-400"
              placeholder="Ask anything..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={state === "submitting" || state === "streaming"}
            />
            <svg
              className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
            {(state === "submitting" || state === "streaming") && (
              <div className="absolute right-4 top-1/2 -translate-y-1/2">
                <div className="animate-spin rounded-full h-5 w-5 border-2 border-slate-300 border-t-blue-500" />
              </div>
            )}
          </div>

          {/* Website input */}
          <div className="relative mt-3">
            <input
              type="url"
              className="w-full py-3 pl-12 pr-4 text-sm border border-slate-200 rounded-xl shadow-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all placeholder:text-slate-400"
              placeholder="Target website (e.g., https://docs.example.com)"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              disabled={state === "submitting" || state === "streaming"}
            />
            <svg
              className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"
              />
            </svg>
          </div>

          {/* Submit button - visible on mobile or when inputs are filled */}
          <button
            type="submit"
            disabled={
              !query.trim() ||
              !website.trim() ||
              state === "submitting" ||
              state === "streaming"
            }
            className="mt-3 w-full py-3 bg-slate-900 text-white rounded-xl font-medium text-sm hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer"
          >
            {state === "submitting"
              ? "Starting search..."
              : state === "streaming"
              ? "Researching..."
              : "Search"}
          </button>
        </form>

        {/* Try example - only when idle */}
        {!hasStarted && (
          <div className="mt-4 text-center">
            <button
              onClick={handleTryExample}
              className="text-sm text-slate-400 hover:text-slate-600 transition-colors cursor-pointer"
            >
              Try: &quot;Explain Quantum Computing&quot;
            </button>
          </div>
        )}
      </div>

      {/* Results Area */}
      {hasStarted && (
        <div className="w-full max-w-2xl mt-8">
          {/* Agent Progress Log */}
          {steps.length > 0 && (
            <div className="mb-8 border-l-2 border-slate-100 pl-4 space-y-2">
              {steps.map((step, i) => {
                const isLatest = i === steps.length - 1;
                const isCompleted =
                  step.type === "function_completed" ||
                  step.type === "allocation_created";
                const isDone =
                  isCompleted || !isLatest || state === "complete";
                const isFailed = step.outcome === "failure";

                return (
                  <div
                    key={i}
                    className="flex items-start gap-2.5 text-sm"
                  >
                    {isFailed ? (
                      <svg
                        className="w-4 h-4 text-red-500 mt-0.5 shrink-0"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M6 18L18 6M6 6l12 12"
                        />
                      </svg>
                    ) : isDone ? (
                      <svg
                        className="w-4 h-4 text-green-500 mt-0.5 shrink-0"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M5 13l4 4L19 7"
                        />
                      </svg>
                    ) : (
                      <span className="mt-1.5 shrink-0">
                        <span className="block h-2.5 w-2.5 bg-blue-500 rounded-full animate-pulse" />
                      </span>
                    )}
                    <span
                      className={
                        isFailed
                          ? "text-red-600"
                          : isDone
                          ? "text-slate-500"
                          : "text-slate-700 font-medium"
                      }
                    >
                      {step.message}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Skeleton while waiting for result */}
          {state === "streaming" && !result && (
            <div className="space-y-3">
              <div className="skeleton h-6 w-3/4" />
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-5/6" />
              <div className="skeleton h-4 w-full mt-4" />
              <div className="skeleton h-4 w-4/5" />
            </div>
          )}

          {/* Error */}
          {state === "error" && error && (
            <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
              <p className="font-medium">Something went wrong</p>
              <p className="mt-1">{error}</p>
            </div>
          )}

          {/* Result */}
          {result && (
            <div className="animate-[fadeIn_0.5s_ease-in]">
              {/* Answer */}
              <div className="prose max-w-none text-slate-800">
                <ReactMarkdown>{result.answer}</ReactMarkdown>
              </div>

              {/* Citations */}
              {result.citations && result.citations.length > 0 && (
                <div className="mt-8 pt-6 border-t border-slate-100">
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
                    Sources
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {result.citations.map((citation, i) => (
                      <a
                        key={i}
                        href={citation.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="group flex items-start gap-3 p-3 rounded-xl border border-slate-100 hover:border-slate-200 hover:bg-slate-50 transition-all no-underline"
                      >
                        <span className="flex items-center justify-center w-6 h-6 rounded-md bg-slate-100 text-slate-500 text-xs font-medium shrink-0 group-hover:bg-blue-50 group-hover:text-blue-600 transition-colors">
                          {i + 1}
                        </span>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-slate-700 truncate group-hover:text-blue-600 transition-colors">
                            {citation.title || "Untitled"}
                          </p>
                          <p className="text-xs text-slate-400 truncate mt-0.5">
                            {new URL(citation.url).hostname}
                          </p>
                        </div>
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <style jsx>{`
        @keyframes fadeIn {
          from {
            opacity: 0;
            transform: translateY(8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </div>
  );
}
