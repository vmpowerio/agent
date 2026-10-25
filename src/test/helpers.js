jest.mock("child_process");
import { EventEmitter } from "events";
import childProcess from "child_process";

module.exports.mockSpawnWithLines = (lines, errorLines = [], exitCode = errorLines.length === 0 ? 0 : 1) => {
  childProcess.spawn.mockImplementation(() => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = jest.fn(() => true);
    process.nextTick(() => {
      lines.forEach((line) => child.stdout.emit("data", Buffer.from(line)));
      // stderr text is data; error events represent stream failures, not diagnostics.
      errorLines.forEach((line) => child.stderr.emit("data", Buffer.from(line)));
      child.emit("close", exitCode, null);
    });
    return child;
  });
};
