/**
 * P2-18/P2-24 — responsive nav disclosure behaviour.
 *
 * Pins the accessibility contract of the mobile menu: the toggle exposes
 * aria-expanded/aria-controls, clicking it opens the panel, and Escape
 * closes it and returns focus to the toggle.
 *
 * Session/bell/account internals are mocked — this test owns only the
 * disclosure behaviour of <Nav /> itself.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { Nav } from "./nav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children?: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("../lib/auth", () => ({
  useAuth: () => ({ status: "anonymous", user: null, signIn: vi.fn(), signOut: vi.fn() }),
}));

vi.mock("./account", () => ({ AccountMenu: () => <div data-testid="account-menu" /> }));
vi.mock("./notifications-bell", () => ({
  NotificationsBell: () => <div data-testid="notifications-bell" />,
}));

function toggle() {
  return screen.getByRole("button", { name: /open menu|close menu/i });
}

function panel() {
  const el = document.getElementById("nav-mobile-menu");
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

describe("Nav mobile disclosure", () => {
  it("starts closed: aria-expanded=false, aria-controls wired, panel hidden", () => {
    render(<Nav />);
    const button = toggle();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", "nav-mobile-menu");
    expect(panel()).toHaveAttribute("hidden");
  });

  it("clicking the hamburger opens the panel (aria-expanded=true, links reachable)", async () => {
    const user = userEvent.setup();
    render(<Nav />);

    await user.click(toggle());

    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(toggle()).toHaveAttribute("aria-label", "Close menu");
    expect(panel()).not.toHaveAttribute("hidden");
    // Panel links are inside the disclosure — reachable and scoped to it.
    const explore = within(panel()).getByRole("link", { name: "Explore" });
    expect(explore).toHaveAttribute("href", "/explore");
  });

  it("Escape closes the panel and returns focus to the toggle", async () => {
    const user = userEvent.setup();
    render(<Nav />);

    await user.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");

    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(panel()).toHaveAttribute("hidden");
    expect(toggle()).toHaveFocus();
  });
});
