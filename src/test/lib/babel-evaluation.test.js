import { transformSync } from "@babel/core";

// use the real compiler's traversal path, without executing its emitted code.
const evaluateDuringCompilation = (source, truthy = false) => {
  let result;
  transformSync(source, {
    babelrc: false,
    configFile: false,
    plugins: [
      () => ({
        visitor: {
          ExpressionStatement (expression) {
            const value = expression.get("expression");
            result = truthy ? value.evaluateTruthy() : value.evaluate();
          }
        }
      })
    ]
  });
  return result;
};

describe("Babel compile-time evaluation boundary", () => {
  it("preserves constant arithmetic and allowed own methods", () => {
    expect(evaluateDuringCompilation("2 + Math.min(3, 5)").value).toEqual(5);
    expect(evaluateDuringCompilation("String(123)").value).toEqual("123");
    expect(evaluateDuringCompilation("1 < 2", true)).toEqual(true);
  });

  it("does not evaluate inherited methods", () => {
    expect(evaluateDuringCompilation("Math.hasOwnProperty('min')").confident).toEqual(false);
  });

  it("does not create functions through an inherited constructor", () => {
    const result = evaluateDuringCompilation("Number.constructor(\"return 'synthetic';\")");
    expect(result.confident).toEqual(false);
    expect(result.value).toBeUndefined();
  });

  it("does not invoke a constructed function during string coercion", () => {
    const result = evaluateDuringCompilation("String({ toString: Number.constructor(\"return 'synthetic';\") })");
    expect(result.confident).toEqual(false);
    expect(result.value).toBeUndefined();
  });

  it("preserves uncertainty for the same coercion through evaluateTruthy", () => {
    const result = evaluateDuringCompilation("String({ toString: Number.constructor(\"return 'synthetic';\") })", true);
    expect(result).toBeUndefined();
  });

  it("does not substitute a global function for a local binding", () => {
    const result = evaluateDuringCompilation("const String = value => value; String(123)");
    expect(result.confident).toEqual(false);
  });
});
