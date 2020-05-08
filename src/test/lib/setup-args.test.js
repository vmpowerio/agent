import { Command } from "commander";
import nconf from "nconf";
import setupArgs from "../../lib/setup-args";

describe("setupArgs", () => {
  let program;
  beforeEach(() => {
    program = new Command();
    nconf.argv().env().file({ file: "path/to/config.json" });
    program.option = jest.fn();
    process.exit = jest.fn();
    program.requiredOption = jest.fn();
  });

  it("when config value `key` is set, it is an optional cli param", () => {
    nconf.set("key", "sk_123456");
    program = setupArgs(program, nconf);

    expect(program.option).toBeCalledWith("-k, --key <key>", "API key");
    expect(program.requiredOption).toBeCalledWith(
      "-s, --secret <secret>",
      "API secret"
    );
    expect(program.requiredOption).toBeCalledWith(
      "-vm, --virtualMachine <vm>",
      "VMPower virtual machine ID"
    );
  });

  it("when config value `secret` is set, it is an optional cli param", () => {
    nconf.set("secret", "sk_123456");
    program = setupArgs(program, nconf);

    expect(program.requiredOption).toBeCalledWith("-k, --key <key>", "API key");
    expect(program.option).toBeCalledWith(
      "-s, --secret <secret>",
      "API secret"
    );
    expect(program.requiredOption).toBeCalledWith(
      "-vm, --virtualMachine <vm>",
      "VMPower virtual machine ID"
    );
  });

  it("when config value `vm_id` is set, it is an optional cli param", () => {
    nconf.set("vm_id", "sk_123456");
    program = setupArgs(program, nconf);

    expect(program.requiredOption).toBeCalledWith("-k, --key <key>", "API key");
    expect(program.requiredOption).toBeCalledWith(
      "-s, --secret <secret>",
      "API secret"
    );
    expect(program.option).toBeCalledWith(
      "-vm, --virtualMachine <vm>",
      "VMPower virtual machine ID"
    );
  });
});
