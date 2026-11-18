import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, afterEach } from "vitest";
import { compactMoney, dt, humanize, money, pct } from "@/lib/format";
import { qs } from "@/lib/utils";
import { api, ApiError, setToken } from "@/lib/api";
import { Pagination, Provenance, RiskBadge, KindBadge, VerificationBadge } from "@/components/domain";

describe("formatting", () => {
  it("formats money, compact money, percentages and dates in UTC", () => {
    expect(money(1234.5)).toBe("$1,234.50");
    expect(money(null)).toBe("—");
    expect(compactMoney(3_310_000)).toBe("$3.31M");
    expect(compactMoney(45_100)).toBe("$45.1k");
    expect(pct(0.9634)).toBe("96.3%");
    expect(dt("2025-09-15T09:30:00+00:00")).toBe("2025-09-15 09:30");
    expect(dt("not a date")).toBe("—");
    expect(humanize("under_investigation")).toBe("Under investigation");
  });
  it("builds query strings without empty values", () => {
    expect(qs({ a: 1, b: "", c: undefined, d: false, e: "x y" })).toBe("?a=1&e=x+y");
    expect(qs({})).toBe("");
  });
});

describe("domain components", () => {
  it("derives risk level from score and labels it in text (not colour alone)", () => {
    render(<><RiskBadge score={85} /><RiskBadge score={65} /><RiskBadge score={35} /><RiskBadge score={5} /></>);
    for (const l of ["critical", "high", "medium", "low"]) expect(screen.getByText(l)).toBeInTheDocument();
  });
  it("distinguishes model predictions from rule indicators and investigator conclusions", () => {
    render(<><KindBadge kind="model" /><KindBadge kind="rule" /><Provenance kind="investigator" /><VerificationBadge status="failed" /></>);
    expect(screen.getByText("Model prediction")).toBeInTheDocument();
    expect(screen.getByText("Rule indicator")).toBeInTheDocument();
    expect(screen.getByText("Investigator conclusion.")).toBeInTheDocument();
    expect(screen.getByText("Integrity failed")).toBeInTheDocument();
  });
  it("paginates", () => {
    const onPage = vi.fn();
    render(<MemoryRouter><Pagination page={2} pageSize={50} total={120} onPage={onPage} /></MemoryRouter>);
    expect(screen.getByText("51–100 of 120")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Next page"));
    expect(onPage).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByLabelText("Previous page"));
    expect(onPage).toHaveBeenCalledWith(1);
  });
});

describe("api client", () => {
  afterEach(() => vi.restoreAllMocks());
  it("sends the bearer token and surfaces sanitised API errors", async () => {
    setToken("tok-123");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "Case is closed" }), { status: 409, headers: { "content-type": "application/json" } }));
    await expect(api("/cases/1/notes", { method: "POST", json: { body: "x" } })).rejects.toMatchObject({ status: 409, message: "Case is closed" });
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers((init as RequestInit).headers).get("Authorization")).toBe("Bearer tok-123");
  });
  it("formats validation errors and clears the token on 401", async () => {
    setToken("tok-123");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Invalid request", errors: [{ loc: ["body", "title"], msg: "too short" }] }), { status: 422, headers: { "content-type": "application/json" } }));
    await expect(api("/cases", { method: "POST", json: {} })).rejects.toThrow("title: too short");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("{}", { status: 401, headers: { "content-type": "application/json" } }));
    await expect(api("/dashboard")).rejects.toBeInstanceOf(ApiError);
    expect(sessionStorage.getItem("fl.token")).toBeNull();
  });
});
