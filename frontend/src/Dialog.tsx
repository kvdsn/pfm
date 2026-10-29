import type React from "react";
import { useId } from "react";

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
  return (
    <div
      className={`modal-backdrop ${backdropClassName}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className={className} role="dialog" aria-modal="true" aria-labelledby={titleId}>
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
