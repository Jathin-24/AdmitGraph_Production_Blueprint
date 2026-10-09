/**
 * P2-13/P2-24 — explore filter ↔ URL round trip.
 *
 * The URL is the single source of truth on /explore (shareable links, clean
 * back button). This test pins both directions:
 *   URL → query: a shared filtered URL drives the exact listPrograms params;
 *   query → URL: changing a filter (or pressing Enter in the search box)
 *               router.replace()s the new query string and resets `page`.
 *
 * next/navigation, next/link and the list endpoints are mocked — no network,
 * no Next runtime.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ExplorePage from "./page";
import { Providers } from "../providers";

const state = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  listPrograms: vi.fn(),
  params: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: state.replaceMock, push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/explore",
  useSearchParams: () => state.params,
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children?: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// Real module (types, ApiError, auth helpers) with only the list endpoints
// swapped for spies, so the whole component graph stays intact.
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    listPrograms: (...args: unknown[]) => state.listPrograms(...args),
    saveProgram: vi.fn(async () => ({ saved: true })),
    unsaveProgram: vi.fn(async () => ({ saved: false })),
  };
});

const PROGRAM = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "M.Sc. Technical AI",
  country_code: "DE",
  degree_type: "M.Sc.",
  field_of_study: "Computer Science",
  official_url: null,
};

function listCall(): Record<string, unknown> | undefined {
  const call = state.listPrograms.mock.calls.find(
    (args) => typeof args[0] === "object" && args[0] !== null
  );
  return call?.[0] as Record<string, unknown> | undefined;
}

beforeEach(() => {
  state.replaceMock.mockClear();
  state.listPrograms.mockReset();
  state.listPrograms.mockResolvedValue({ items: [PROGRAM], total: 1, page: 1, page_size: 20 });
  state.params = new URLSearchParams();
});

describe("explore filters ↔ URL", () => {
  it("URL → query: a shared filtered URL drives the exact listPrograms params", async () => {
    state.params = new URLSearchParams(
      "q=inf&country=DE&degree_level=M.Sc.&sort=name&page=2&saved_only=true"
    );
    render(
      <Providers>
        <ExplorePage />
      </Providers>
    );

    await waitFor(() => expect(listCall()).toBeDefined());
    expect(listCall()).toEqual({
      page: 2,
      page_size: 20,
      saved_only: true,
      q: "inf",
      country: "DE",
      degree_level: "M.Sc.",
      sort: "name",
    });
  });

  it("query → URL: picking a country replaces the URL and resets the page", async () => {
    const user = userEvent.setup();
    render(
      <Providers>
        <ExplorePage />
      </Providers>
    );
    await waitFor(() => expect(listCall()).toBeDefined());
    // Facets are derived from the loaded results — wait for the DE option
    // the fixture's country produced before interacting with the select.
    await waitFor(() => expect(screen.getByRole("option", { name: "DE" })).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Country"), "DE");

    expect(state.replaceMock).toHaveBeenCalledWith("/explore?country=DE", { scroll: false });
  });

  it("query → URL: Enter in the search box writes q and clears the page param", async () => {
    state.params = new URLSearchParams("page=3");
    const user = userEvent.setup();
    render(
      <Providers>
        <ExplorePage />
      </Providers>
    );
    await waitFor(() => expect(listCall()).toBeDefined());

    await user.type(
      screen.getByPlaceholderText("Program, university or city…"),
      "technical{Enter}"
    );

    expect(state.replaceMock).toHaveBeenCalledWith("/explore?q=technical", { scroll: false });
  });

  it("invalid sort values in the URL normalize to fit_score (never crash)", async () => {
    state.params = new URLSearchParams("sort=bogus");
    render(
      <Providers>
        <ExplorePage />
      </Providers>
    );

    await waitFor(() => expect(listCall()).toBeDefined());
    expect(listCall()?.sort).toBe("fit_score");
    // …and the sort select shows the normalized option, not the raw junk.
    expect(screen.getByLabelText("Sort by")).toHaveValue("fit_score");
  });
});
