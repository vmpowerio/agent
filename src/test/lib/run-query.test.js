// mock here so import sorting cannot capture the real process API before helpers load.
jest.mock("child_process");
import childProcess from "child_process";
import { mockSpawnWithLines } from "../helpers";
import runQuery from "../../lib/run-query";

describe("runQuery", () => {
    it("parses lines where user has logged out", async () => {
      mockSpawnWithLines([
          "USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\r\n",
          ">steve                 console             1  Active      none   5 / 13 / 2020 12: 57 PM\r\n",
        ]);
        const result = await runQuery();
        expect(childProcess.spawn).toHaveBeenCalledWith("query", ["user"]);
        expect(result.data.length).toEqual(1);
        expect(result.data[0]).toContain(">steve");
        expect(result.error).toBeUndefined();
    });
    it("parses lines where user is still logged in", async () => {
      mockSpawnWithLines([
        "USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\r\n",
        ">vmpower               rdp-tcp#3           2  Active          .  5/21/2020 4:38 AM"
      ]);
      const result = await runQuery();
      expect(result.data.length).toEqual(1);
      expect(result.data[0]).toContain(">vmpower");
      expect(result.error).toBeUndefined();
    });
    it("resolves with error when there are erorr lines", async () => {
      mockSpawnWithLines(
          [
              "vmpower  pts/0        10.0.0.23        2020-05-10T21:17:15+0000   still logged in\n",
              "vmpower  pts/0        10.0.0.23        2020-05-10T04:11:45+0000 - 2020-05-10T04:12:21+0000  (00:00)\n",
          ],
          ["error message 123"],
          2
      );
        const result = await runQuery();
        expect(result.error).toEqual("error message 123");
    });
    it("accepts Windows query exit code one and strips the header", async () => {
      mockSpawnWithLines(["USERNAME SESSIONNAME ID STATE\r\n"], [], 1);
      expect(await runQuery()).toEqual({ data: [] });
    });
    it("returns a non-success exit code when stderr is empty", async () => {
      mockSpawnWithLines([], [], 2);
      expect(await runQuery()).toEqual({ error: "error code 2" });
    });
});
