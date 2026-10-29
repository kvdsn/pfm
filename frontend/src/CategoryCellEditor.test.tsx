import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CategoryCellEditor } from "./CategoryCellEditor";

const choices = [
  { id: 5, name: "Uncategorized" },
  { id: 6, name: "Groceries" },
  { id: 7, name: "Housing" },
];

function renderEditor(props: Partial<React.ComponentProps<typeof CategoryCellEditor>> = {}) {
  const onChoose = vi.fn();
  const onClose = vi.fn();
  const utils = render(
    <CategoryCellEditor
      choices={choices}
      currentCategoryId={5}
      suggestedCategoryId={null}
      disabled={false}
      onChoose={onChoose}
      onClose={onClose}
      {...props}
    />,
  );
  return { ...utils, onChoose, onClose, input: screen.getByLabelText("Category") };
}

afterEach(cleanup);

const highlighted = () => screen.getAllByRole("option").find((option) => option.getAttribute("aria-selected") === "true");

describe("CategoryCellEditor", () => {
  it("focuses the field and highlights the current category", () => {
    const { input } = renderEditor({ currentCategoryId: 7 });
    expect(input).toHaveFocus();
    expect(highlighted()).toHaveTextContent("Housing");
  });

  it("highlights the suggested category, also when the suggestion arrives later", () => {
    const { rerender, onChoose, onClose, input } = renderEditor();
    expect(highlighted()).toHaveTextContent("Uncategorized");
    rerender(
      <CategoryCellEditor
        choices={choices}
        currentCategoryId={5}
        suggestedCategoryId={6}
        disabled={false}
        onChoose={onChoose}
        onClose={onClose}
      />,
    );
    expect(highlighted()).toHaveTextContent("Groceriessuggested");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChoose).toHaveBeenCalledWith(6);
  });

  it("filters as you type and assigns the highlighted match with Enter", () => {
    const { onChoose, input } = renderEditor();
    fireEvent.change(input, { target: { value: "o" } });
    expect(screen.getAllByRole("option").map((option) => option.firstChild?.textContent))
      .toEqual(["Uncategorized", "Groceries", "Housing"]);
    fireEvent.change(input, { target: { value: "hou" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChoose).toHaveBeenCalledWith(7);
  });

  it("moves the highlight with the arrow keys and closes on Escape or leaving the field", () => {
    const { onChoose, onClose, input } = renderEditor();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChoose).toHaveBeenCalledWith(6);
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.blur(input);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("assigns a clicked option", () => {
    const { onChoose } = renderEditor();
    fireEvent.click(screen.getByRole("option", { name: "Housing" }));
    expect(onChoose).toHaveBeenCalledWith(7);
  });
});
