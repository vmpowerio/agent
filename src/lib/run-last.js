// @flow
import os from "os";
import { spawn } from "child_process";

export type RunLastResult = {
  data?: Array<string>,
  error?: string,
};

const runLast = (): Promise<RunLastResult> => {
  return new Promise((resolve) => {
    let result = "";
    let error = "";
    let settled = false;
    const finish = (value: RunLastResult) => {
      if (!settled) {
        settled = true;
        resolve(value);
      }
    };
    const message = (failure: Error): string => {
      return failure instanceof Error && failure.message.trim() ? failure.message : "process error";
    };

    try {
      // fetch last 10 logins
      const last = spawn("last", ["-10", "--time-format", "iso"]);
      const streamError = (stream: string, failure: Error) => {
        if (settled) {
          return;
        }
        finish({ error: `last ${stream} failed: ${message(failure)}` });
        try {
          last.kill();
        } catch {
          // the child may already have exited; retain the original failure.
        }
      };
      last.on("error", (failure) => {
        finish({ error: `last failed: ${message(failure)}` });
      });
      last.stdout.on("error", (failure) => streamError("stdout", failure));
      last.stderr.on("error", (failure) => streamError("stderr", failure));
      last.stdout.on("data", (data) => {
        if (!settled) {
          result += data;
        }
      });
      last.stderr.on("data", (data) => {
        if (!settled) {
          error += data;
        }
      });

      last.on("close", (code, signal) => {
        if (signal || code !== 0) {
          const fallback = signal ? `terminated by signal ${signal}` : `error code ${code}`;
          finish({ error: error.trim() ? error : fallback });
        } else {
          // parse result
          const lines = result.split(os.EOL).filter((line) => line.length > 0);
          finish({ data: lines });
        }
      });
    } catch (failure) {
      finish({ error: `last failed: ${message(failure)}` });
    }
  });
};

module.exports = runLast;
