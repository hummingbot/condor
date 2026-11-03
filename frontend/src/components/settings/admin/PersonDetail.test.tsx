/**
 * What the person panel offers, and what it refuses to offer (FEAT-088).
 *
 * The server is the gate: `share_server` refuses a target who is not approved,
 * refuses the server's own owner, and cannot express an ownership transfer at
 * all. None of that is enforced here — a control this panel renders is still
 * answered by the refusal table in `routes/admin.py`.
 *
 * What *is* enforced here is that the panel never offers a control whose only
 * possible outcome is a refusal, and never hides one whose outcome is the whole
 * point of the row. A disabled grant with the reason written next to it is the
 * difference between "not yet" and "broken"; a revoke button missing from the
 * orphan row would leave live access with nothing in the product able to remove
 * it. Those are the two failure modes worth a test.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminPerson } from "@/lib/admin-api";

import { PersonDetail } from "./PersonDetail";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

const BASE: AdminPerson = {
  user_id: 815494756,
  display_name: "Yago Carvalho",
  username: "YagoCarvalho",
  first_name: "Yago",
  last_name: "Carvalho",
  role: "pending",
  is_admin: false,
  code_run: false,
  created_at: 1772506318,
  approved_at: 0,
  approved_by: null,
  last_seen: 0,
  servers: [
    { server: "brigado_2", permission: "", implicit: false },
    { server: "local", permission: "", implicit: false },
  ],
  known: true,
};

function render(person: AdminPerson, onSetServerAccess = vi.fn()) {
  act(() => {
    root.render(
      <PersonDetail
        person={person}
        onSetRole={vi.fn()}
        onSetServerAccess={onSetServerAccess}
        onSetCodeRun={vi.fn()}
        rolePending={false}
        pendingServer={null}
      />,
    );
  });
  return onSetServerAccess;
}

/** Every grant button for one server, in render order: [No access, Trader]. */
function grantButtons(server: string): HTMLButtonElement[] {
  const row = [...container.querySelectorAll("div")].find(
    (el) => el.children.length === 2 && el.firstElementChild?.textContent === server,
  );
  if (!row) throw new Error(`no grid row for '${server}'`);
  return [...row.querySelectorAll("button")];
}

function click(button: HTMLButtonElement) {
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a person who is not approved yet", () => {
  it("cannot be granted a server", () => {
    render(BASE);

    const [, trader] = grantButtons("brigado_2");
    expect(trader.textContent).toBe("Trader");
    expect(trader.disabled).toBe(true);
  });

  it("says why the grid is disabled instead of just greying out", () => {
    render(BASE);

    expect(container.textContent).toContain(
      "Approve this person before granting server access",
    );
  });

  it("offers the approval that unlocks it, in the same panel", () => {
    render(BASE);

    const approve = [...container.querySelectorAll("button")].find(
      (b) => b.textContent === "Approved",
    );
    expect(approve?.disabled).toBe(false);
  });

  it("can be rejected outright while the request is still pending", () => {
    render(BASE);

    expect(
      [...container.querySelectorAll("button")].some((b) => b.textContent === "Reject"),
    ).toBe(true);
  });
});

describe("when the person was last seen", () => {
  // The admin API sends `last_seen = 0` for a person who has never opened the
  // bot, and 0 is a real epoch, not a blank. Before ARCH-304 the panel had its
  // own formatter that read 0 as "never"; it now shares `formatRelativeTime`
  // with the rest of the dashboard, which does not — so the panel coerces.
  it("says 'never' for a person the bot has never seen", () => {
    render({ ...BASE, role: "user", last_seen: 0 });

    expect(container.textContent).toContain("last seen never");
    expect(container.textContent).not.toContain("d ago");
  });

  it("reads in the same short units as the rest of the dashboard", () => {
    render({ ...BASE, role: "user", last_seen: Date.now() / 1000 - 7200 });

    expect(container.textContent).toContain("last seen 2h ago");
  });

  it("has no last seen at all for an id with no user record", () => {
    render({ ...BASE, known: false, last_seen: 0 });

    expect(container.textContent).not.toContain("last seen");
  });
});

describe("a person who has just been approved", () => {
  const approved: AdminPerson = { ...BASE, role: "user", approved_at: 1772509999 };

  it("can be granted a server without a reload", () => {
    render(BASE);
    expect(grantButtons("brigado_2")[1].disabled).toBe(true);

    // The mutation answers with the person as they now are, and the row
    // re-renders from that — this is that second render.
    render(approved);

    expect(grantButtons("brigado_2")[1].disabled).toBe(false);
  });

  it("no longer carries the reason the grid was locked", () => {
    render(approved);

    expect(container.textContent).not.toContain("Approve this person before");
  });

  it("sends the grant as the destination permission", () => {
    const onSet = render(approved);

    click(grantButtons("brigado_2")[1]);

    expect(onSet).toHaveBeenCalledWith("brigado_2", "trader");
  });

  it("cannot be rejected once approved — blocking is the move", () => {
    render(approved);

    expect(
      [...container.querySelectorAll("button")].some((b) => b.textContent === "Reject"),
    ).toBe(false);
  });
});

describe("a person who already has access", () => {
  const granted: AdminPerson = {
    ...BASE,
    role: "user",
    servers: [
      { server: "brigado_2", permission: "trader", implicit: false },
      { server: "local", permission: "", implicit: false },
    ],
  };

  it("shows the granted state as the active one", () => {
    render(granted);

    const [noAccess, trader] = grantButtons("brigado_2");
    expect(trader.getAttribute("aria-pressed")).toBe("true");
    expect(noAccess.getAttribute("aria-pressed")).toBe("false");
  });

  it("revokes by sending the empty permission", () => {
    const onSet = render(granted);

    click(grantButtons("brigado_2")[0]);

    expect(onSet).toHaveBeenCalledWith("brigado_2", "");
  });

  it("does not re-send a grant they already hold", () => {
    render(granted);

    expect(grantButtons("brigado_2")[1].disabled).toBe(true);
  });
});

describe("an admin", () => {
  const admin: AdminPerson = {
    ...BASE,
    display_name: "Federico Cardoso",
    role: "admin",
    is_admin: true,
    servers: [
      { server: "brigado_2", permission: "owner", implicit: true },
      { server: "local", permission: "owner", implicit: true },
    ],
  };

  it("reads as reaching everything by role, not by grant", () => {
    render(admin);

    expect(container.textContent).toContain("all servers · by role");
  });

  it("offers no toggle that revoking could not honour", () => {
    render(admin);

    expect(grantButtons("brigado_2")).toHaveLength(0);
  });

  it("has no code_run checkbox — they pass that gate on the role", () => {
    render(admin);

    expect(container.querySelector("input[type=checkbox]")).toBeNull();
  });

  it("says where the role actually comes from", () => {
    render(admin);

    expect(container.textContent).toContain("ADMIN_USER_ID");
  });
});

describe("an id that holds a grant but has no user record", () => {
  const orphan: AdminPerson = {
    user_id: 6483117755,
    display_name: "User 6483117755",
    username: "",
    first_name: "",
    last_name: "",
    role: "",
    is_admin: false,
    code_run: false,
    created_at: 0,
    approved_at: 0,
    approved_by: null,
    last_seen: 0,
    servers: [
      { server: "brigado_2", permission: "", implicit: false },
      { server: "local", permission: "trader", implicit: false },
    ],
    known: false,
  };

  it("explains that the access is live and the record is gone", () => {
    render(orphan);

    expect(container.textContent).toContain("no user record");
  });

  it("can still be revoked — the only thing that row is for", () => {
    const onSet = render(orphan);

    const [noAccess] = grantButtons("local");
    expect(noAccess.disabled).toBe(false);

    click(noAccess);
    expect(onSet).toHaveBeenCalledWith("local", "");
  });

  it("cannot be granted anything more", () => {
    render(orphan);

    expect(grantButtons("brigado_2")[1].disabled).toBe(true);
  });

  it("offers no role controls for a registration that does not exist", () => {
    render(orphan);

    expect(
      [...container.querySelectorAll("button")].some((b) => b.textContent === "Approved"),
    ).toBe(false);
  });
});
