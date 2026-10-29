import { useCallback, useEffect, useRef, useState } from "react";

type ToastKind = "notice" | "error";
type ToastMessage = { kind: ToastKind; text: string; id: number };

const DURATION_MS: Record<ToastKind, number> = { notice: 3500, error: 7000 };

/**
 * One brief message at a time, shown over the page so content never moves.
 * Setting an empty message hides the current message of that kind.
 */
export function useToast() {
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const nextId = useRef(0);

  const show = useCallback((kind: ToastKind, text: string) => {
    if (text) {
      nextId.current += 1;
      setToast({ kind, text, id: nextId.current });
    } else {
      setToast((current) => (current?.kind === kind ? null : current));
    }
  }, []);
  const setNotice = useCallback((text: string) => show("notice", text), [show]);
  const setError = useCallback((text: string) => show("error", text), [show]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), DURATION_MS[toast.kind]);
    return () => clearTimeout(timer);
  }, [toast]);

  return { toast, setNotice, setError, dismiss: () => setToast(null) };
}

export function Toast({ toast, onDismiss }: { toast: ToastMessage | null; onDismiss: () => void }) {
  return (
    <div className="toast-region" aria-live="polite">
      {toast && (
        <div
          key={toast.id}
          className={`toast ${toast.kind === "error" ? "error-message" : "success-message"}`}
          role={toast.kind === "error" ? "alert" : "status"}
        >
          <span>{toast.text}</span>
          <button className="toast-close" aria-label="Dismiss message" onClick={onDismiss}>×</button>
        </div>
      )}
    </div>
  );
}
