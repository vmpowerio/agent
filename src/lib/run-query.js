// @flow
import os from "os";
import { spawn } from "child_process";

export type RunLastResult = {
  data?: Array<string>,
  error?: string,
};

const runLast = (): Promise<RunLastResult> => {
  return new Promise((resolve) => {
    // fetch currently logged in users
    const args = ["user"];
    const last = spawn("query", args);

    let result = "";
    let error = "";
    last.stdout.on("data", (data) => {
      result += data;
    });

    last.stderr.on("error", (data) => {
      error += data;
    });

    last.on("close", (code) => {
      // code = 1 on Windows is not an error code https://docs.microsoft.com/en-us/windows/deployment/usmt/usmt-return-codes
      if (code > 1) {
        if (!error) {
          error = `error code ${code}`;
        }
        return resolve({ error: error });
      } else {
        // parse result. The first line is a header row so we throw that out
        const lines = result.
          split(os.EOL).
          filter((line) => line.length > 0).
          slice(1);
        return resolve({ data: lines });
      }
    });
  });
};

module.exports = runLast;
