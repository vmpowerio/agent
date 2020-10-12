jest.mock("needle");
import { Command } from "commander";
import nconf from "nconf";
import needle from "needle";
import sendLoginInfo from "../../lib/send-login-info";

describe("setupArgs", () => {

  let program;
  const mockData = {
    data: [
      "vmpower  pts/0        10.0.0.23        2020-05-10T18:10:05+0000 - 2020-05-10T18:36:38+0000  (00:26)\n",
      "vmpower  pts/0        10.0.0.23        2020-05-10T04:11:45+0000 - 2020-05-10T04:12:21+0000  (00:00)\n",
    ],
  };

  beforeEach(() => {
    program = new Command();
    nconf.argv().env().file({ file: "path/to/config.json" });
    nconf.set("vm_id", "resource-group/unit-test-machine");
    nconf.set("key", "29872189472108947123");
    nconf.set("secret", "sk_1234");
    Date.now = jest.fn(() => 500);
    const log = console.log;
    const error = console.log;
    console.log = jest.fn((data) => log(data));
    console.error = jest.fn((data) => error(data));
  });

  it("sends login info lines", async () => {
    needle.mockResolvedValue({
      statusCode: 200,
    });
    await sendLoginInfo(program, mockData);
    expect(needle).toBeCalledWith(
      "post",
      "https://api.vmpower.io/login-data?vm=resource-group/unit-test-machine",
      {
        data: mockData.data,
        platform: process.platform,
        systemTime: 500,
      },
      {
        headers: {
          "x-api-key": "29872189472108947123",
          "x-api-secret": "sk_1234",
        },
        json: true
      }
    );
    expect(console.error).not.toBeCalledWith(
      "successfully sent login data for vm_id=resource-group/unit-test-machine"
    );
  });

  it("logs error with bad api response", async () => {
    needle.mockResolvedValue({
      status: 400,
      body: {
        message: "test failure",
      },
    });
    await sendLoginInfo(program, mockData);
    expect(needle).toBeCalledWith(
      "post",
      "https://api.vmpower.io/login-data?vm=resource-group/unit-test-machine",
      {
        data: mockData.data,
        platform: process.platform,
        systemTime: 500,
      },
      {
        headers: {
          "x-api-key": "29872189472108947123",
          "x-api-secret": "sk_1234",
        },
        json: true
      }
    );
    expect(console.error).toBeCalledWith(
      "failed to send login data for vm_id=resource-group/unit-test-machine message={\"message\":\"test failure\"}"
    );
  });
});
