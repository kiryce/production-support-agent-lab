import { describe, expect, it } from "vitest";
import {
  DEFAULT_CONSOLE_URL_STATE,
  parseConsoleState,
  serializeConsoleState,
  type ConsoleUrlState
} from "../src/shared/consoleState";

describe("console URL state", () => {
  it("parses a shareable incident investigation URL", () => {
    const state = parseConsoleState(
      "?runId=run_123&alertKey=agent%3Aorder%3ATIMEOUT&workspace=tools&tab=tool-audit&severity=P1&status=active&q=timeout&sort=newest&new=1"
    );

    expect(state).toEqual({
      runId: "run_123",
      alertKey: "agent:order:TIMEOUT",
      workspace: "tools",
      tab: "tool-audit",
      severity: "P1",
      status: "active",
      query: "timeout",
      sort: "newest",
      onlyNew: true
    });
  });

  it("serializes only state that matters for sharing", () => {
    const state: ConsoleUrlState = {
      ...DEFAULT_CONSOLE_URL_STATE,
      runId: "run_123",
      alertKey: "agent:order:TIMEOUT",
      workspace: "alerts",
      tab: "triage",
      query: "timeout",
      onlyNew: true
    };

    expect(serializeConsoleState(state)).toBe(
      "runId=run_123&alertKey=agent%3Aorder%3ATIMEOUT&tab=triage&q=timeout&new=1"
    );
  });

  it("roundtrips non-default queue filters", () => {
    const original: ConsoleUrlState = {
      runId: null,
      alertKey: "agent:billing:PII",
      workspace: "alerts",
      tab: "brief",
      severity: "P0",
      status: "investigating",
      query: "lin",
      sort: "count",
      onlyNew: true
    };

    expect(parseConsoleState(serializeConsoleState(original))).toEqual(original);
  });

  it("roundtrips the settings workspace", () => {
    const original: ConsoleUrlState = {
      ...DEFAULT_CONSOLE_URL_STATE,
      workspace: "settings"
    };

    expect(serializeConsoleState(original)).toBe("workspace=settings");
    expect(parseConsoleState("workspace=settings")).toEqual(original);
  });

  it("falls back to safe defaults for invalid URL values", () => {
    const state = parseConsoleState(
      "?workspace=metrics&tab=raw&severity=P9&status=done&sort=random&new=0"
    );

    expect(state).toEqual(DEFAULT_CONSOLE_URL_STATE);
  });
});
