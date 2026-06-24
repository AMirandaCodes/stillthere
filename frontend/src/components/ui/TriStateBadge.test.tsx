import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import TriStateBadge from "./TriStateBadge";

describe("TriStateBadge", () => {
  it.each([
    ["yes", "Yes"],
    ["no", "No"],
    ["unclear", "Unclear"],
  ] as const)("renders '%s' with label '%s'", (value, label) => {
    render(<TriStateBadge value={value} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("applies green colour classes for 'yes'", () => {
    const { container } = render(<TriStateBadge value="yes" />);
    expect(container.firstChild).toHaveClass("bg-green-100", "text-green-800");
  });

  it("applies red colour classes for 'no'", () => {
    const { container } = render(<TriStateBadge value="no" />);
    expect(container.firstChild).toHaveClass("bg-red-100", "text-red-800");
  });

  it("applies grey colour classes for 'unclear'", () => {
    const { container } = render(<TriStateBadge value="unclear" />);
    expect(container.firstChild).toHaveClass("bg-gray-100", "text-gray-600");
  });

  it("merges extra className onto the badge span", () => {
    const { container } = render(<TriStateBadge value="yes" className="mt-2" />);
    expect(container.firstChild).toHaveClass("mt-2");
  });
});
