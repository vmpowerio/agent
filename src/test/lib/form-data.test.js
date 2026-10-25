import crypto from "crypto";

// exercise the actual multipart dependency selected by Jest's jsdom caller.
const FormData = require(require.resolve("form-data", {
  paths: [require.resolve("jsdom")]
}));

describe("jsdom multipart security", () => {
  it("uses cryptographic entropy rather than Math.random for boundaries", () => {
    const random = jest.spyOn(Math, "random").mockImplementation(() => {
      throw new Error("insecure entropy used");
    });
    const entropy = jest.spyOn(crypto, "randomBytes").mockReturnValue(Buffer.alloc(12, 1));
    try {
      const form = new FormData();
      expect(form.getBoundary()).toEqual("--------------------------010101010101010101010101");
      expect(entropy).toHaveBeenCalledWith(12);
      expect(random).not.toHaveBeenCalled();
    } finally {
      random.mockRestore();
      entropy.mockRestore();
    }
  });

  it("escapes field names before writing multipart headers", () => {
    const form = new FormData();
    form.append("field\"\r\nX-Injected: yes", "safe");
    const body = form.getBuffer().toString();
    expect(body).toContain("name=\"field%22%0D%0AX-Injected: yes\"");
    expect(body).not.toContain("\r\nX-Injected:");
  });

  it("escapes filenames before writing multipart headers", () => {
    const form = new FormData();
    form.append("upload", Buffer.from("safe"), {
      filename: "fixture\"\r\nX-Injected: yes.txt"
    });
    const body = form.getBuffer().toString();
    expect(body).toContain("filename=\"fixture%22%0D%0AX-Injected: yes.txt\"");
    expect(body).not.toContain("\r\nX-Injected:");
  });

  it("prevents a field name from introducing an extra multipart part", () => {
    const form = new FormData();
    form.setBoundary("synthetic-boundary");
    form.append("field\"\r\n\r\nvalue\r\n--synthetic-boundary\r\nContent-Disposition: form-data; name=\"admin", "safe");
    const body = form.getBuffer().toString();
    expect(body.split("\r\n--synthetic-boundary")).toHaveLength(2);
    expect(body.match(/Content-Disposition:/gu)).toHaveLength(2);
    expect(body).not.toContain("\r\nContent-Disposition: form-data; name=\"admin");
  });

  it("preserves ordinary text and buffer serialization, headers and length", () => {
    const form = new FormData();
    form.setBoundary("synthetic-boundary");
    form.append("text", "fixture");
    form.append("upload", Buffer.from("bytes"), { filename: "fixture.txt" });
    const body = form.getBuffer();
    expect(form.getHeaders()["content-type"]).toEqual("multipart/form-data; boundary=synthetic-boundary");
    expect(form.getLengthSync()).toEqual(body.length);
    expect(body.toString()).toContain("name=\"text\"\r\n\r\nfixture\r\n");
    expect(body.toString()).toContain("filename=\"fixture.txt\"\r\nContent-Type: text/plain\r\n\r\nbytes\r\n");
    expect(body.toString()).toMatch(/--synthetic-boundary--\r\n$/u);
  });
});
