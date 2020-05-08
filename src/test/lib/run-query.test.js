import { mockSpawnWithLines } from "../helpers";
import runLast from "../../lib/run-last";

describe("runQuery", () => {
    it("parses lines where user has logged out", async () => {
      mockSpawnWithLines([
          "USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\r\n",
          ">steve                 console             1  Active      none   5 / 13 / 2020 12: 57 PM\r\n",
        ]);
        const result = await runLast();
        expect(result.data.length).toEqual(2);
        expect(result.error).toBeUndefined();
    });
    it("parses lines where user is still logged in", async () => {
      mockSpawnWithLines([
        "USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\r\n",
        ">vmpower               rdp-tcp#3           2  Active          .  5/21/2020 4:38 AM"
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
