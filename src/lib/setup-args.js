// @flow
import typeof { Command } from "commander";
import typeof { nconf as Nconf } from "nconf";

import fs from "fs";

const setupArgs = (program: Command, nconf: Nconf): Command => {
  if (fs.existsSync("\\ProgramData\\vmpower\\vmpower.conf") && process.platform === "win32") {
    nconf.file("\\ProgramData\\vmpower\\vmpower.conf");
  } else if (fs.existsSync("/etc/vmpower.conf")) {
    nconf.file("/etc/vmpower.conf");
  }

  if (!nconf.get("key")) {
    program.requiredOption("-k, --key <key>", "API key");
  } else {
    program.option("-k, --key <key>", "API key");
  }
  if (!nconf.get("secret")) {
    program.requiredOption("-s, --secret <secret>", "API secret");
  } else {
    program.option("-s, --secret <secret>", "API secret");
  }
  if (!nconf.get("vm_id")) {
    program.requiredOption(
      "-vm, --virtualMachine <vm>",
      "VMPower virtual machine ID"
    );
  } else {
    program.option("-vm, --virtualMachine <vm>", "VMPower virtual machine ID");
  }

  program.option("-h, --host <host>", "API host");

  return program;
};

module.exports = setupArgs;
