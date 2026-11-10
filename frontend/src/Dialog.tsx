import type React from "react";
import { useEffect, useId, useRef } from "react";

/** A modal dialog over a dimmed backdrop; clicking the backdrop or the close button closes it. */
export function Dialog({
  title,
  subtitle,
  closeLabel,
  className,
  backdropClassName,
  onClose,
  children,
}: {
  title: string;
  subtitle?: React.ReactNode;
  closeLabel: string;
  className: string;
  backdropClassName: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const titleId = useId();
  const panel = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  // Escape closes the topmost modal dialog only; page-level Escape handlers skip while one is open.
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const open = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      if (open[open.length - 1] !== panel.current) return;
      // Listeners added after this one would run once the dialog is gone and take the same Escape for the page.
      event.stopImmediatePropagation();
      close.current();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  return (
    <div
      className={`modal-backdrop ${backdropClassName}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section ref={panel} className={className} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="section-heading">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle}
          </div>
          <button className="icon-button" aria-label={closeLabel} onClick={onClose}>×</button>
        </div>
        {children}
      </section>
    </div>
  );
}
