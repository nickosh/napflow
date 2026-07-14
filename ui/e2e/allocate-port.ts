import { execFileSync } from "node:child_process";

/**
 * Return an explicit override or ask the OS for a fresh loopback port.
 *
 * Playwright evaluates its config synchronously, so a tiny child process owns
 * the asynchronous `listen(0)` call. It never accepts a connection; closing
 * the reservation therefore leaves no TIME_WAIT socket before the web server
 * immediately binds the selected port.
 */
export function allocateLoopbackPort(override?: string): string {
  if (override !== undefined) return override;
  const script = [
    'const {createServer}=require("node:net");',
    "const server=createServer();",
    'server.listen(0,"127.0.0.1",()=>{',
    "const address=server.address();",
    'if(typeof address!=="object"||address===null)process.exit(2);',
    "process.stdout.write(String(address.port));",
    "server.close();",
    "});",
  ].join("");
  const port = execFileSync(process.execPath, ["-e", script], {
    encoding: "utf-8",
  }).trim();
  if (!/^\d+$/.test(port)) throw new Error(`invalid allocated port: ${port}`);
  return port;
}
