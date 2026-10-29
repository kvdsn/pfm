import { useEffect, useRef, useState } from "react";

export type CategoryChoice = { id: number | null; name: string };

/**
 * Inline autocomplete in a transaction's category cell. Typing filters the categories,
 * up/down moves the highlight, Enter assigns the highlighted category, and Escape or
 * leaving the field closes it. The suggested category starts highlighted.
 */
export function CategoryCellEditor({
  choices,
  currentCategoryId,
  suggestedCategoryId,
  disabled,
  onChoose,
  onClose,
}: {
  choices: CategoryChoice[];
  currentCategoryId: number | null;
  suggestedCategoryId: number | null;
  disabled: boolean;
  onChoose: (categoryId: number | null) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const initialIndex = () => {
    const suggested = choices.findIndex((choice) => choice.id === suggestedCategoryId);
    return suggested >= 0 ? suggested : Math.max(0, choices.findIndex((choice) => choice.id === currentCategoryId));
  };
  const [highlighted, setHighlighted] = useState(initialIndex);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const filtered = choices.filter((choice) =>
    choice.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  const optionId = (choice: CategoryChoice) => `category-cell-option-${choice.id ?? "uncategorized"}`;

  useEffect(() => input.current?.focus(), []);
  // A suggestion can arrive after the editor opened; highlight it unless the user is filtering.
  useEffect(() => {
    if (!search) setHighlighted(initialIndex());
  }, [suggestedCategoryId]);
  useEffect(() => {
    list.current?.querySelector(".highlighted")?.scrollIntoView?.({ block: "nearest" });
  }, [highlighted, search]);

  return (
    <div className="category-cell-editor" onClick={(event) => event.stopPropagation()}>
      <input
        ref={input}
        aria-label="Category"
        role="combobox"
        aria-expanded="true"
        aria-controls="category-cell-options"
        aria-activedescendant={filtered[highlighted] ? optionId(filtered[highlighted]) : undefined}
        placeholder="Type a category…"
        value={search}
        disabled={disabled}
        onChange={(event) => {
          setSearch(event.target.value);
          setHighlighted(0);
        }}
        onBlur={onClose}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!filtered.length) return;
            const step = event.key === "ArrowDown" ? 1 : -1;
            setHighlighted((index) => Math.max(0, Math.min(filtered.length - 1, index + step)));
          } else if (event.key === "Enter") {
            event.preventDefault();
            event.stopPropagation();
            const choice = filtered[highlighted];
            if (choice && !disabled) onChoose(choice.id);
          } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            onClose();
          }
        }}
      />
      <div className="category-cell-options" id="category-cell-options" role="listbox" aria-label="Categories" ref={list}>
        {filtered.length ? filtered.map((choice, index) => (
          <div
            id={optionId(choice)}
            className={`category-cell-option${index === highlighted ? " highlighted" : ""}`}
            key={choice.id ?? "uncategorized"}
            role="option"
            aria-selected={index === highlighted}
            // Keep focus in the field so choosing with the mouse does not close the editor first.
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => setHighlighted(index)}
            onClick={() => !disabled && onChoose(choice.id)}
          >
            <span>{choice.name}</span>
            {choice.id === suggestedCategoryId && <span className="suggested-tag">suggested</span>}
            {choice.id === currentCategoryId && <span className="current-tag">current</span>}
          </div>
        )) : (
          <p className="muted category-cell-empty">No matching categories.</p>
        )}
      </div>
    </div>
  );
}
