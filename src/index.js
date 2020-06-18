// @flow
import { Command } from "commander";
import nconf from "nconf";
import runLast from "./lib/run-last";
import runQuery from "./lib/run-query";
import sendLoginInfo from "./lib/send-login-info";
import setupArgs from "./lib/setup-args";

let program = new Command();
program.version("0.0.2");

program = setupArgs(program, nconf);
program.parse(process.argv);

let initialRun = true;

const loginInfoApp = process.platform === "win32" ? runQuery : runLast;

const run = async () => {
  if (initialRun) {
    const result = await loginInfoApp();
    await sendLoginInfo(program, result);
    initialRun = false;
  }
  setTimeout(async () => {
    const result = await loginInfoApp();
    await sendLoginInfo(program, result);
    run();
  }, 60 * 1000);
};

run();
