import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as backupPost } from "../app/api/console/event-store/backups/route";
import { GET as operationsGet } from "../app/api/console/event-store/operations/route";
import { POST as restoreDrillPost } from "../app/api/console/event-store/restore-drills/route";
import { POST as retentionPost } from "../app/api/console/event-store/retention/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("event-store operations BFF routes", () => {
  it("lists operation ledger rows with sanitized query filters", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse([
        {
          id: "evt_op_1",
          tenant_id: "demo_tenant",
          actor_user_id: "operator",
          operation: "backup",
          status: "completed",
          summary: { schema_version: "event_store_operation_summary.v1" },
          created_at: "2026-07-05T00:00:00Z"
        }
      ])
    );

    const response = await operationsGet(
      getRequest(
        "/api/console/event-store/operations?operation=backup&status=completed&limit=9999&order=sideways&created_after=2026-07-05"
      )
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      records: [
        {
          id: "evt_op_1",
          tenant_id: "demo_tenant",
          actor_user_id: "operator",
          operation: "backup",
          status: "completed",
          summary: { schema_version: "event_store_operation_summary.v1" },
          created_at: "2026-07-05T00:00:00Z"
        }
      ],
      limit: 500,
      order: "desc"
    });
    const [target] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(url.pathname).toBe("/api/v1/admin/event-store/operations");
    expect(url.searchParams.get("operation")).toBe("backup");
    expect(url.searchParams.get("status")).toBe("completed");
    expect(url.searchParams.get("limit")).toBe("500");
    expect(url.searchParams.get("order")).toBe("desc");
    expect(url.searchParams.get("created_after")).toBe("2026-07-05");
  });

  it("creates verified backups without forwarding arbitrary paths", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        source_path: "events.db",
        backup_path: "backups/support-agent-lab-demo.db",
        size_bytes: 4096,
        page_count: 1,
        started_at: "2026-07-05T00:00:00Z",
        completed_at: "2026-07-05T00:00:01Z",
        verified: true,
        verification_detail: "quick_check=ok",
        backup_token: "backup.token"
      })
    );

    const response = await backupPost(
      jsonRequest("/api/console/event-store/backups", {
        label: "../../release",
        path: "C:/should/not/forward.db",
        overwrite: true,
        verify: false
      })
    );

    expect(response.status).toBe(200);
    const [target, init] = fetchMock.mock.calls[0];
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/event-store/backups");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      label: "../../release",
      overwrite: false,
      verify: true
    });
  });

  it("clamps retention day fields and defaults to dry-run", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        tenant_id: "demo_tenant",
        dry_run: true,
        include_events: false,
        vacuum_requested: false,
        vacuum_performed: false,
        started_at: "2026-07-05T00:00:00Z",
        completed_at: "2026-07-05T00:00:01Z",
        tables: [],
        total_candidates: 0,
        total_deleted: 0,
        preview_token: "preview.token"
      })
    );

    const response = await retentionPost(
      jsonRequest("/api/console/event-store/retention", {
        dry_run: "please apply",
        include_events: true,
        vacuum: true,
        event_retention_days: 1,
        tool_audit_retention_days: 9999,
        idempotency_retention_days: -5,
        alert_delivery_retention_days: 3
      })
    );

    expect(response.status).toBe(200);
    const [target, init] = fetchMock.mock.calls[0];
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/event-store/retention");
    expect(JSON.parse(String(init?.body))).toEqual({
      dry_run: true,
      include_events: true,
      vacuum: true,
      event_retention_days: 30,
      tool_audit_retention_days: 3650,
      idempotency_retention_days: 1,
      alert_delivery_retention_days: 7
    });
  });

  it("runs restore drills only from server-issued backup tokens", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        backup_path: "backups/support-agent-lab-demo.db",
        restore_path: "scratch/support-agent-lab-demo.db",
        restore_path_retained: false,
        size_bytes: 4096,
        page_count: 1,
        started_at: "2026-07-05T00:00:00Z",
        completed_at: "2026-07-05T00:00:01Z",
        verified: true,
        verification_detail: "quick_check=ok; required schema present; restore health_check passed",
        health_check_passed: true,
        table_counts: { events: 1 },
        high_water_mark: { events: { row_count: 1, max_rowid: 1 } },
        restore_drill_token: "restore.token"
      })
    );

    const response = await restoreDrillPost(
      jsonRequest("/api/console/event-store/restore-drills", {
        backup_token: "backup.token",
        backup_path: "C:/should/not/forward.db",
        restore_path: "C:/also/not/forward.db",
        overwrite: true
      })
    );

    expect(response.status).toBe(200);
    const [target, init] = fetchMock.mock.calls[0];
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/event-store/restore-drills");
    expect(JSON.parse(String(init?.body))).toEqual({
      backup_token: "backup.token"
    });
  });

  it("rejects restore drills without a backup token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await restoreDrillPost(
      jsonRequest("/api/console/event-store/restore-drills", {
        backup_path: "backups/support-agent-lab-demo.db"
      })
    );

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      detail: "Verified backup token is required for restore drill."
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects retention apply without server-issued gate tokens", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await retentionPost(
      jsonRequest("/api/console/event-store/retention", {
        dry_run: false,
        include_events: true,
        apply_confirmed: true
      })
    );

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      detail: "Verified backup token, restore drill token, preview token, and confirmation are required."
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards retention apply only with backup, restore drill, and preview tokens", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        tenant_id: "demo_tenant",
        dry_run: false,
        include_events: true,
        vacuum_requested: true,
        vacuum_performed: false,
        started_at: "2026-07-05T00:00:00Z",
        completed_at: "2026-07-05T00:00:01Z",
        tables: [],
        total_candidates: 4,
        total_deleted: 4,
        preview_token: null
      })
    );

    const response = await retentionPost(
      jsonRequest("/api/console/event-store/retention", {
        dry_run: false,
        include_events: true,
        vacuum: true,
        event_retention_days: 365,
        backup_token: "backup.token",
        restore_drill_token: "restore.token",
        preview_token: "preview.token",
        apply_confirmed: true
      })
    );

    expect(response.status).toBe(200);
    const [target, init] = fetchMock.mock.calls[0];
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/event-store/retention");
    expect(JSON.parse(String(init?.body))).toEqual({
      dry_run: false,
      include_events: true,
      vacuum: true,
      event_retention_days: 365,
      backup_token: "backup.token",
      restore_drill_token: "restore.token",
      preview_token: "preview.token",
      apply_confirmed: true
    });
  });
});

function jsonRequest(path: string, body: unknown) {
  return new Request(`http://console.local${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }) as unknown as NextRequest;
}

function getRequest(path: string) {
  return { nextUrl: new URL(`http://console.local${path}`) } as unknown as NextRequest;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
