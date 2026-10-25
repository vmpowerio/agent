const braces = require(require.resolve("braces", {
  paths: [require.resolve("micromatch")]
}));
const uuid = require(require.resolve("uuid", {
  paths: [require.resolve("node-notifier")]
}));
const json5 = require("json5");
const minimatch = require("minimatch");
const semver = require("semver");
const flatted = require("flatted");
const yaml = require("js-yaml");
const notifierUtils = require("node-notifier/lib/utils");
const WindowsToaster = require("node-notifier/notifiers/toaster");

describe("updated development dependency contracts", () => {
  it("bounds brace parsing while preserving ordinary expansion", () => {
    expect(braces.expand("logs/{info,warn}.{log,txt}")).toEqual(["logs/info.log", "logs/info.txt", "logs/warn.log", "logs/warn.txt"]);
    // a bounded plain string avoids exercising the old unbalanced-brace loop.
    expect(() => braces.parse("a".repeat(65537))).toThrow();
  });

  it("keeps JSON5 prototype keys as data rather than changing object inheritance", () => {
    const value = json5.parse("{unquoted: 'value', __proto__: {injected: true}}");
    expect(Reflect.getPrototypeOf(value)).toBe(Object.prototype);
    expect(value.unquoted).toBe("value");
    expect(Reflect.apply(Object.prototype.hasOwnProperty, value, ["__proto__"])).toBe(true);
    expect(value.injected).toBeUndefined();
  });

  it("preserves glob, version range, YAML, and circular lint-cache behavior", () => {
    expect(minimatch("logs/info.log", "logs/*.{log,txt}")).toBe(true);
    expect(minimatch("logs/.private.log", "logs/*.log")).toBe(false);
    expect(semver.satisfies("18.20.8", ">=18 <19")).toBe(true);
    expect(semver.satisfies("24.22.1", ">=18 <19")).toBe(false);
    expect(yaml.safeLoad("enabled: true\nitems:\n  - one\n")).toEqual({
      enabled: true,
      items: ["one"]
    });
    const cache = { name: "synthetic" };
    cache.self = cache;
    const restored = flatted.parse(flatted.stringify(cache));
    expect(restored.self).toBe(restored);
    expect(restored.name).toBe("synthetic");
  });

  it("rejects truncated UUID output buffers without partially modifying them", () => {
    const namespace = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
    [uuid.v3, uuid.v5].forEach((generate) => {
      const output = Buffer.alloc(8, 170);
      expect(() => generate("synthetic", namespace, output, 4)).toThrow(RangeError);
      expect(output).toEqual(Buffer.alloc(8, 170));
    });
  });

  it("preserves deterministic UUIDs and valid caller-provided buffers", () => {
    const namespace = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
    const expected = uuid.v5("synthetic", namespace);
    expect(uuid.v5("synthetic", namespace)).toBe(expected);
    const output = Buffer.alloc(18, 170);
    expect(uuid.v5("synthetic", namespace, output, 2)).toBe(output);
    expect(output.subarray(0, 2)).toEqual(Buffer.alloc(2, 170));
    expect(output.subarray(2).toString("hex")).toBe(expected.replace(/-/gu, ""));
  });

  it("keeps the real Windows notifier UUID caller working without opening pipes or sending notifications", async () => {
    const pipe = jest.spyOn(notifierUtils, "createNamedPipe").mockResolvedValue(Buffer.alloc(0));
    const command = jest.spyOn(notifierUtils, "fileCommand").mockImplementation(jest.fn());
    try {
      const notifier = new WindowsToaster({ withFallback: false });
      notifier.notify({ message: "synthetic test only" });
      await Promise.resolve();
      expect(pipe).toHaveBeenCalledTimes(1);
      const argument = pipe.mock.calls[0][0];
      const name = typeof argument === "string" ? argument : argument.namedPipe;
      expect(name.startsWith("\\\\.\\pipe\\notifierPipe-")).toBe(true);
      expect(name.slice("\\\\.\\pipe\\notifierPipe-".length)).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
      expect(command).toHaveBeenCalledTimes(1);
    } finally {
      pipe.mockRestore();
      command.mockRestore();
    }
  });
});
