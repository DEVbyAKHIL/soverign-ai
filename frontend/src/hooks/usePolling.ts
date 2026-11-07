import { useEffect, useRef, useState } from "react";

export type Live<T> = { data: T | null; error: string | null; updatedAt: Date | null };

/**
 * Re-fetch every `intervalMs` (default 5 s) while the tab is visible; abort in-flight requests on
 * unmount or when `deps` change. The transport is isolated here on purpose: switching to
 * Server-Sent Events or WebSockets later means replacing this hook, not the pages.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[] = [], intervalMs = 5000,
): Live<T> {
  const [state, setState] = useState<Live<T>>({ data: null, error: null, updatedAt: null });
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  useEffect(() => {
    let ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const tick = async () => {
      if (document.visibilityState === "visible") {
        ctrl = new AbortController();
        try {
          const data = await fetchRef.current(ctrl.signal);
          if (!stopped) setState({ data, error: null, updatedAt: new Date() });
        } catch (e) {
          if (!stopped && (e as Error).name !== "AbortError") {
            setState((s) => ({ ...s, error: (e as Error).message }));
          }
        }
      }
      if (!stopped) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => { stopped = true; ctrl.abort(); clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs]);

  return state;
}
