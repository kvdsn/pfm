import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";

afterEach(cleanup);

describe("Dialog", () => {
  it("closes on Escape, and only the topmost dialog does", () => {
    const closeOuter = vi.fn();
    const closeInner = vi.fn();
    render(
      <>
        <Dialog title="Outer" closeLabel="Close outer" className="a" backdropClassName="b" onClose={closeOuter}>x</Dialog>
        <Dialog title="Inner" closeLabel="Close inner" className="a" backdropClassName="b" onClose={closeInner}>y</Dialog>
      </>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(closeInner).toHaveBeenCalledTimes(1);
    expect(closeOuter).not.toHaveBeenCalled();
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
  });
});
