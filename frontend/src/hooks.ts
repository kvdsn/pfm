import type React from "react";
import { useEffect, useRef, useState } from "react";

/** Calls `onEscape` when Escape is pressed anywhere while `active` is true. */
export function useEscapeKey(active: boolean, onEscape: () => void) {
  const handler = useRef(onEscape);
  handler.current = onEscape;
  useEffect(() => {
    if (!active) return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") handler.current();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [active]);
}

/** True when keyboard input is going to a text field or other editable control. */
/** Whether keys typed at `target` belong to it. Checkboxes and radios take no text, so shortcuts still apply there. */
export const isEditableTarget = (target: EventTarget | null) =>
  target instanceof HTMLElement
  && (target.isContentEditable
    || ["SELECT", "TEXTAREA"].includes(target.tagName)
    || (target instanceof HTMLInputElement && !["checkbox", "radio"].includes(target.type)));

/**
 * Up and down arrows move a selection through `ids`, in the order given, and clamp at the ends.
 * `select` receives the newly selected id. Arrows do nothing while an input or select has focus.
 */
/**
 * A filter field's keydown handler: Escape clears the filter and leaves the field, and goes
 * no further, so it does not also unselect or close a view.
 */
export function clearOnEscape(clear: () => void) {
  return (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    event.nativeEvent.stopImmediatePropagation();
    clear();
    event.currentTarget.blur();
  };
}

/** Up and Down move the selection through `ids`; `enabled` false leaves the keys to another list on the page. */
export function useArrowSelection<T>(ids: T[], current: T | null | undefined, select: (id: T) => void, enabled = true) {
  useEffect(() => {
    const handleArrow = (event: KeyboardEvent) => {
      if (!enabled || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || isEditableTarget(event.target)) return;
      // A dialog over the view (such as the shortcuts or the balance graph) takes the keys.
      if (!ids.length || document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      const index = current === null || current === undefined ? -1 : ids.indexOf(current);
      const step = event.key === "ArrowDown" ? 1 : -1;
      const next = ids[Math.min(ids.length - 1, Math.max(0, index < 0 ? (step > 0 ? 0 : ids.length - 1) : index + step))];
      event.preventDefault();
      select(next);
    };
    window.addEventListener("keydown", handleArrow);
    return () => window.removeEventListener("keydown", handleArrow);
  });
}

/** Calls `onSignal` each time `signal` changes after the first render, such as a counter raised by a shortcut. */
export function useSignal(signal: number, onSignal: () => void) {
  const handler = useRef(onSignal);
  handler.current = onSignal;
  const seen = useRef(signal);
  useEffect(() => {
    if (signal === seen.current) return;
    seen.current = signal;
    handler.current();
  }, [signal]);
}

/** Tracks an element's content size; pass the element via a callback ref (`ref={setElement}`). */
export function useElementSize(element: HTMLElement | null) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return size;
}
