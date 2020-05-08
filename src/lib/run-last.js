// @flow
import os from "os";
import { spawn } from "child_process";

export type RunLastResult = {
  data?: Array<string>,
  error?: string,
};

const runLast = (): Promise<RunLastResult> => {
  return new Promise((resolve) => {
    // fetch last 10 logins
    const args = ["-10", "--time-format", "iso"];
    const last = spawn("last", args);

    let result = "";
    let error = "";
    last.stdout.on("data", (data) => {
      result += data;
    });

    last.stderr.on("error", (data) => {
      error += data;
    });

    last.on("close", (code) => {
      if (code !== 0) {
        return resolve({ error: error });
      } else {
        // parse result
        const lines = result.split(os.EOL).filter((line) => line.length > 0);
        return resolve({ data: lines });
      }
    });
  });
};

module.exports = runLast;
