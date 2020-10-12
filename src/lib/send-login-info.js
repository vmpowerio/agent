// @flow
import typeof { Command } from "commander";
import type { RunLastResult } from "./run-last";

import nconf from "nconf";
import needle from "needle";

const sendLoginInfo = async (program: Command, runResult: RunLastResult) => {
  const host = nconf.get("host") || program.host || "api.vmpower.io";
  const apiKey = nconf.get("key") || program.key;
  const apiSecret = nconf.get("secret") || program.secret;
  const vm = nconf.get("vm_id") || program.virtualMachine;

  if (runResult.error && runResult.error.length) {
    console.error("error running command", runResult);
    return runResult.error;
  }
  try {
    const response = await needle("post",
      host.includes("localhost:")
        ? `http://${host}/login-data?vm=${vm}`
        : `https://${host}/login-data?vm=${vm}`,
      {
        data: runResult.data,
        systemTime: Date.now(),
        platform: process.platform,
        version: program.version()
      },
      {
        headers: {
          "x-api-key": apiKey,
          "x-api-secret": apiSecret,
        },
        json: true
      }
    );
    if (response.statusCode === 200) {
      console.log(`successfully sent login data for vm_id=${vm}`);
    } else {
      console.error(
        `failed to send login data for vm_id=${vm} message=${
          response.body ? JSON.stringify(response.body) : ""
        }`
      );
    }
  } catch (error) {
    console.error(error.message);
  }
};

module.exports = sendLoginInfo;
