#!/usr/bin/env node
/**
 * Dependency-free cipher evaluator for Swiphie.
 *
 * Python owns discovery, derivation, validation and registry writes.  This
 * helper only evaluates untrusted player.js in a Node vm with a small browser
 * shim and returns the deobfuscated URLs as JSON.  It never touches the
 * network or the filesystem beyond stdin/stdout.
 */
import fs from "node:fs";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

const N_PROBE_INPUT = "KdrqFlzJXl9EcCwlmEy";
const PLAYER_IIFE_TRAILER = "})(_yt_player);";
const VALID_N_RESULT = /^[a-zA-Z0-9_-]+$/;

const request = JSON.parse(fs.readFileSync(0, "utf8"));
const result = evaluate(request);
process.stdout.write(JSON.stringify(result), () => process.exit(0));

function evaluate(input) {
  const { playerJs, sigExpr, nClass, signatureCiphers } = input;
  const nExpr = buildNJsExpression(nClass);
  const sigStmt =
    `window._cipherSigFunc = function(sig){ try { return ${
      sigExpr.replaceAll("INPUT", "sig")
    }; } catch(e){ return null; } };`;
  const nStmt =
    `window._nTransformFunc = function(n){ try { return ${
      nExpr.replaceAll("INPUT", "n")
    }; } catch(e){ return n; } };`;
  const exportCode = `; ${sigStmt} ${nStmt} `;
  const injectionMode = playerJs.includes(PLAYER_IIFE_TRAILER)
    ? "iife"
    : "global-fallback";
  const modified = injectionMode === "iife"
    ? playerJs.replaceAll(
      PLAYER_IIFE_TRAILER,
      `${exportCode} ${PLAYER_IIFE_TRAILER}`,
    )
    : `${playerJs}\n${exportCode}`;

  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);

  let initError = null;
  try {
    vm.runInContext(modified, context, { timeout: 10000 });
  } catch (error) {
    initError = errorMessage(error);
  }

  const sigFn = context._cipherSigFunc;
  const nFn = context._nTransformFunc;
  const nProbe = (() => {
    try {
      const output = typeof nFn === "function" ? nFn(N_PROBE_INPUT) : undefined;
      const valid = typeof output === "string" &&
        output !== N_PROBE_INPUT &&
        output.length >= 5 &&
        VALID_N_RESULT.test(output);
      return {
        in: N_PROBE_INPUT,
        out: output == null ? undefined : String(output),
        changed: !!(output && output !== N_PROBE_INPUT),
        valid,
      };
    } catch (error) {
      return { error: errorMessage(error), valid: false };
    }
  })();

  const evaluation = {
    initError,
    injectionMode,
    sigAvailable: typeof sigFn === "function",
    nAvailable: typeof nFn === "function",
    nProbe,
  };

  try {
    evaluation.urls = signatureCiphers.map((cipher) =>
      deobfuscate(cipher, sigFn, nFn)
    );
  } catch (error) {
    evaluation.error = errorMessage(error);
  }
  return evaluation;
}

function buildNJsExpression(nClass) {
  return "(function(n){try{var u=new g." + nClass +
    "('https://x.googlevideo.com/videoplayback?n='+n,true);" +
    "var t=u.get('n');return(t&&t!==n)?t:n;}catch(e){return n;}})(INPUT)";
}

function deobfuscate(signatureCipher, sigFn, nFn) {
  if (typeof sigFn !== "function" || typeof nFn !== "function") {
    throw new Error("cipher functions unavailable");
  }
  const params = {};
  for (const pair of signatureCipher.split("&")) {
    const index = pair.indexOf("=");
    if (index > 0) {
      params[decodeURIComponent(pair.slice(0, index))] = decodeURIComponent(
        pair.slice(index + 1),
      );
    }
  }
  const s = params.s;
  const sp = params.sp || "signature";
  const url = params.url;
  if (s == null || url == null) throw new Error("missing s/url");
  const sig = sigFn(s);
  if (sig == null) throw new Error("sig returned null");
  let output = `${url}${url.includes("?") ? "&" : "?"}${sp}=${
    encodeURIComponent(String(sig))
  }`;
  const nMatch = output.match(/[?&]n=([^&]+)/);
  if (nMatch?.[1]) {
    const transformed = nFn(decodeURIComponent(nMatch[1]));
    if (transformed == null) throw new Error("n returned null");
    output = output.replace(
      /([?&])n=[^&]+/,
      `$1n=${encodeURIComponent(String(transformed))}`,
    );
  }
  return output;
}

function createSandbox() {
  const element = () => makeElement();
  const documentStub = {
    createElement: element,
    createElementNS: element,
    createTextNode: () => ({}),
    getElementsByTagName: () => [],
    getElementsByClassName: () => [],
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    documentElement: makeElement(),
    body: makeElement(),
    head: makeElement(),
    currentScript: null,
    cookie: "",
    readyState: "complete",
    visibilityState: "visible",
    hidden: false,
  };
  const locationStub = {
    href: "https://www.youtube.com/",
    origin: "https://www.youtube.com",
    protocol: "https:",
    host: "www.youtube.com",
    hostname: "www.youtube.com",
    pathname: "/",
    search: "",
    hash: "",
    assign() {},
    replace() {},
    reload() {},
  };
  const storageStub = {
    getItem: () => null,
    setItem() {},
    removeItem() {},
    clear() {},
    key: () => null,
    length: 0,
  };
  const silentConsole = {
    log() {},
    info() {},
    warn() {},
    error() {},
    debug() {},
    trace() {},
    dir() {},
    table() {},
  };
  const sandbox = {
    console: silentConsole,
    document: documentStub,
    navigator: {
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      language: "en-US",
      languages: ["en-US", "en"],
      platform: "Win32",
      maxTouchPoints: 0,
      userAgentData: { brands: [], mobile: false, platform: "Windows" },
    },
    location: locationStub,
    screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1080 },
    history: { pushState() {}, replaceState() {}, back() {}, forward() {} },
    localStorage: storageStub,
    sessionStorage: storageStub,
    XMLHttpRequest: class {
      open() {}
      send() {}
      setRequestHeader() {}
      abort() {}
      addEventListener() {}
      removeEventListener() {}
      getAllResponseHeaders() {
        return "";
      }
    },
    fetch: async () => ({
      ok: false,
      status: 0,
      text: async () => "",
      json: async () => ({}),
    }),
    performance: {
      now: () => Number(process.hrtime.bigint() / 1000000n),
    },
    crypto: webcrypto,
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    btoa: (value) => Buffer.from(value, "binary").toString("base64"),
    TextEncoder,
    TextDecoder,
    URL,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    queueMicrotask,
    requestAnimationFrame: (callback) => setTimeout(() => callback(0), 0),
    cancelAnimationFrame: (handle) => clearTimeout(handle),
    addEventListener() {},
    removeEventListener() {},
    postMessage() {},
    trustedTypes: {
      createPolicy: () => ({
        createScriptURL: (value) => value,
        createHTML: (value) => value,
        createScript: (value) => value,
      }),
    },
    ytcfg: { get: () => undefined, set() {} },
    _yt_player: {},
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.top = sandbox;
  sandbox.parent = sandbox;
  sandbox.frames = sandbox;
  return sandbox;
}

function makeElement() {
  return {
    style: {},
    dataset: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains: () => false,
    },
    appendChild() {},
    removeChild() {},
    insertBefore() {},
    setAttribute() {},
    getAttribute: () => null,
    removeAttribute() {},
    addEventListener() {},
    removeEventListener() {},
    getContext: () => null,
    play: () => Promise.resolve(),
    pause() {},
    load() {},
    canPlayType: () => "",
    getBoundingClientRect: () => ({
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
    focus() {},
    blur() {},
    click() {},
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}
