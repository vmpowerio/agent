jest.mock("child_process");
import childProcess from "child_process";

module.exports.mockSpawnWithLines = (lines, errorLines = [], exitCode = errorLines.length === 0 ? 0 : 1) => {
  childProcess.spawn.mockImplementation(() => {
    const stdoutOnHandlers = [];
    const stderrOnHandlers = [];

    return {
      stdout: {
        on: (type, handler) => {
          expect(type).toEqual("data");
          stdoutOnHandlers.push(handler);
        },
      },
      stderr: {
        on: (type, handler) => {
          expect(type).toEqual("error");
          stderrOnHandlers.push(handler);
        },
      },
      on: (type, handler) => {
        if (type !== "close") {
          return;
        }
        stdoutOnHandlers.forEach((handler) => {
          lines.forEach((line) => {
            handler(line);
          });
        });
        stderrOnHandlers.forEach((handler) => {
          errorLines.forEach((line) => {
            handler(line);
          });
        });
        handler(exitCode);
      },
    };
  });
};
