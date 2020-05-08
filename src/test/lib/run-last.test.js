import { mockSpawnWithLines } from "../helpers";
import runLast from "../../lib/run-last";

describe("runLast", () => {
  it("parses lines where user has logged out", async () => {
    mockSpawnWithLines([
      "vmpower  pts/0        10.0.0.23        2020-05-10T18:10:05+0000 - 2020-05-10T18:36:38+0000  (00:26)\n",
      "vmpower  pts/0        10.0.0.23        2020-05-10T04:11:45+0000 - 2020-05-10T04:12:21+0000  (00:00)\n",
    ]);
    const result = await runLast();
    expect(result.data.length).toEqual(2);
    expect(result.error).toBeUndefined();
  });
  it("parses lines where user is still logged in", async () => {
    mockSpawnWithLines([
      "vmpower  pts/0        10.0.0.23        2020-05-10T21:17:15+0000   still logged in\n",
      "vmpower  pts/0        10.0.0.23        2020-05-10T04:11:45+0000 - 2020-05-10T04:12:21+0000  (00:00)\n",
    ]);
    const result = await runLast();
    expect(result.data.length).toEqual(2);
    expect(result.error).toBeUndefined();
  });
  it("resolves with error when there are erorr lines", async () => {
    mockSpawnWithLines(
      [
        "vmpower  pts/0        10.0.0.23        2020-05-10T21:17:15+0000   still logged in\n",
        "vmpower  pts/0        10.0.0.23        2020-05-10T04:11:45+0000 - 2020-05-10T04:12:21+0000  (00:00)\n",
      ],
      ["error message 123"]
    );
    const result = await runLast();
    expect(result.error).toEqual("error message 123");
  });
});
