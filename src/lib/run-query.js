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
      // fetch currently logged in users
      const last = spawn("query", ["user"]);
      const streamError = (stream: string, failure: Error) => {
        if (settled) {
          return;
        }
        finish({ error: `query ${stream} failed: ${message(failure)}` });
        try {
          last.kill();
        } catch {
          // the child may already have exited; retain the original failure.
        }
      };
      last.on("error", (failure) => {
        finish({ error: `query failed: ${message(failure)}` });
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
        // preserve query's existing no-user exit-code-one behavior.
        if (signal || code !== 0 && code !== 1) {
          const fallback = signal ? `terminated by signal ${signal}` : `error code ${code}`;
          finish({ error: error.trim() ? error : fallback });
        } else {
          // parse result. the first line is a header row so we throw that out.
          const lines = result.
            split(os.EOL).
            filter((line) => line.length > 0).
            slice(1);
          finish({ data: lines });
        }
      });
    } catch (failure) {
      finish({ error: `query failed: ${message(failure)}` });
    }
  });
};

module.exports = runLast;
