jest.mock("child_process");
jest.mock("needle");
import { Command } from "commander";
import { EventEmitter } from "events";
import childProcess from "child_process";
import nconf from "nconf";
import needle from "needle";
import runLast from "../../lib/run-last";
import runQuery from "../../lib/run-query";
import sendLoginInfo from "../../lib/send-login-info";

const collectors = [
  {
    name: "last",
    collect: runLast,
    args: ["-10", "--time-format", "iso"],
    output: "synthetic user\n",
    rows: ["synthetic user"]
  },
  {
    name: "query",
    collect: runQuery,
    args: ["user"],
    output: "USERNAME STATE\nsynthetic user\n",
    rows: ["synthetic user"]
  }
];

collectors.forEach((collector) => {
  describe(`${collector.name} process failures and reporting`, () => {
    let child;
    let program;

    beforeEach(() => {
      child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      child.kill = jest.fn(() => true);
      childProcess.spawn.mockReturnValue(child);
      needle.mockReset();
      needle.mockResolvedValue({ statusCode: 200 });
      program = new Command().version("0.0.2");
      program.virtualMachine = "synthetic-vm";
      nconf.overrides({
        host: "synthetic.invalid",
        key: "synthetic-key",
        secret: "synthetic-secret"
      });
      jest.spyOn(console, "log").mockImplementation(jest.fn());
      jest.spyOn(console, "error").mockImplementation(jest.fn());
    });

    afterEach(() => {
      jest.restoreAllMocks();
    });

    const expectNoReport = async (result) => {
      // exercise reporting first so the old empty-error result attempts a mocked request.
      const returned = await sendLoginInfo(program, result);
      expect(needle).not.toHaveBeenCalled();
      expect(result.error).toEqual(expect.any(String));
      expect(result.error.trim().length).toBeGreaterThan(0);
      expect(result.data).toBeUndefined();
      expect(returned).toBe(result.error);
    };

    it("preserves chunked stdout and the successful reporting payload", async () => {
      const pending = collector.collect();
      expect(childProcess.spawn).toHaveBeenCalledWith(collector.name, collector.args);
      child.stdout.emit("data", Buffer.from(collector.output.slice(0, 3)));
      child.stdout.emit("data", Buffer.from(collector.output.slice(3)));
      child.stderr.emit("data", Buffer.from("nonfatal diagnostic"));
      child.emit("close", 0, null);
      const result = await pending;
      expect(result).toEqual({ data: collector.rows });
      await sendLoginInfo(program, result);
      expect(needle).toHaveBeenCalledTimes(1);
      expect(needle).toHaveBeenCalledWith("post", "https://synthetic.invalid/login-data?vm=synthetic-vm", {
        data: collector.rows,
        systemTime: expect.any(Number),
        platform: process.platform,
        version: "0.0.2"
      }, {
        headers: {
          "x-api-key": "synthetic-key",
          "x-api-secret": "synthetic-secret"
        },
        json: true
      });
    });

    it("collects stderr text from data events on a failed exit", async () => {
      const pending = collector.collect();
      child.stderr.emit("data", Buffer.from("synthetic "));
      child.stderr.emit("data", Buffer.from("failure"));
      child.emit("close", 2, null);
      const result = await pending;
      await expectNoReport(result);
      expect(result.error).toBe("synthetic failure");
    });

    it("does not report partial stdout when failure has no stderr", async () => {
      expect.hasAssertions();
      const pending = collector.collect();
      child.stdout.emit("data", Buffer.from(collector.output));
      child.emit("close", 2, null);
      await expectNoReport(await pending);
    });

    it("uses a nonempty fallback for whitespace-only failure diagnostics", async () => {
      expect.hasAssertions();
      const pending = collector.collect();
      child.stderr.emit("data", Buffer.from(" \n\t"));
      child.emit("close", 2, null);
      await expectNoReport(await pending);
    });

    it("returns a collection error when spawn throws synchronously", async () => {
      expect.hasAssertions();
      childProcess.spawn.mockImplementationOnce(() => {
        throw new Error("synthetic spawn failure");
      });
      await expectNoReport(await collector.collect());
    });

    it("handles an asynchronous spawn error before a later successful close", async () => {
      expect.hasAssertions();
      const pending = collector.collect();
      child.emit("error", new Error("synthetic ENOENT"));
      child.stdout.emit("data", Buffer.from(collector.output));
      child.emit("close", 0, null);
      await expectNoReport(await pending);
    });

    it("handles empty process error messages without waiting for close", async () => {
      expect.hasAssertions();
      const pending = collector.collect();
      child.emit("error", new Error(""));
      await expectNoReport(await pending);
    });

    ["stdout", "stderr"].forEach((stream) => {
      it(`handles ${stream} errors independently from data and stops its child`, async () => {
        const pending = collector.collect();
        child[stream].emit("error", new Error("synthetic stream failure"));
        child.emit("close", 0, null);
        child[stream].emit("error", new Error("late stream error"));
        const result = await pending;
        await expectNoReport(result);
        expect(result.error).toContain(`${stream} failed`);
        expect(child.kill).toHaveBeenCalledTimes(1);
      });
    });

    it("retains the stream failure if terminating the child throws", async () => {
      child.kill.mockImplementationOnce(() => {
        throw new Error("already exited");
      });
      const pending = collector.collect();
      child.stdout.emit("error", new Error("original stream failure"));
      const result = await pending;
      await expectNoReport(result);
      expect(result.error).toContain("original stream failure");
    });

    it("rejects signal termination even with partial stdout", async () => {
      expect.hasAssertions();
      const pending = collector.collect();
      child.stdout.emit("data", Buffer.from(collector.output));
      child.emit("close", null, "SIGTERM");
      await expectNoReport(await pending);
    });

    it("rejects a missing exit status without a signal", async () => {
      expect.hasAssertions();
      const pending = collector.collect();
      child.emit("close", null, null);
      await expectNoReport(await pending);
    });

    if (collector.name === "query") {
      it("preserves the known exit-one no-user result", async () => {
        const pending = collector.collect();
        child.stderr.emit("data", Buffer.from("No user exists for *"));
        child.emit("close", 1, null);
        const result = await pending;
        expect(result).toEqual({ data: [] });
        await sendLoginInfo(program, result);
        expect(needle).toHaveBeenCalledTimes(1);
        expect(needle.mock.calls[0][2].data).toEqual([]);
      });
    }
  });
});
