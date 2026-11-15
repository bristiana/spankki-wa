// Cloudflare Worker in front of the Python app. One container instance ("main") holds the demo state,
// so WhatsApp, the confirm pages and the web chat all see the same balance.
import { DurableObject } from "cloudflare:workers";

const PORT = 8080;
const INACTIVITY_TIMEOUT_MS = 60 * 60 * 1000; // keep the demo warm for an hour after the last request

const SECRET_KEYS = ["ANTHROPIC_API_KEY", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_APP_SECRET", "WA_VERIFY_TOKEN",
  "DEMO_PHONE", "WA_DISPLAY_NUMBER", "DEMO_PROACTIVE", "EB_APP_ID", "EB_PRIVATE_KEY",
  "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_DAILY_CHARS"] as const;

export class Coach extends DurableObject<any> {
  private starting: Promise<void> | undefined;
  private monitoring: Promise<void> | undefined;

  constructor(ctx: DurableObjectState, env: any) {
    super(ctx, env);
    const container = (ctx as any).container;
    if (container?.running) {
      void ctx.blockConcurrencyWhile(() => container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS));
      this.observeExit();
    }
  }

  async fetch(request: Request): Promise<Response> {
    // /admin/restart?key=<WA_VERIFY_TOKEN>: stop the container so the next request starts it with fresh secrets.
    const u = new URL(request.url);
    if (u.pathname.startsWith("/__state/")) return this.state(request, u);
    if (u.pathname === "/admin/restart") {
      if (!this.env.WA_VERIFY_TOKEN || u.searchParams.get("key") !== this.env.WA_VERIFY_TOKEN) {
        return new Response("forbidden", { status: 403 });
      }
      const c = (this.ctx as any).container;
      if (c?.running) await c.destroy("restart requested");
      return new Response("restarted; next request boots a fresh container\n");
    }
    this.starting ??= this.startAndWait().finally(() => { this.starting = undefined; });
    await this.starting;
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return (this.ctx as any).container.getTcpPort(PORT).fetch(forwarded);
  }

  // The app's state files, kept in this object's storage so a redeploy (a new container) picks them up again.
  // Served only by the "state" object, which never starts a container; guarded by WA_VERIFY_TOKEN.
  private async state(request: Request, u: URL): Promise<Response> {
    if (!this.env.WA_VERIFY_TOKEN || u.searchParams.get("key") !== this.env.WA_VERIFY_TOKEN) {
      return new Response("forbidden", { status: 403 });
    }
    const name = u.pathname.slice("/__state/".length);
    if (!/^[A-Za-z0-9_.-]{1,80}$/.test(name)) return new Response("bad name", { status: 400 });
    if (request.method === "GET") {
      const v = await this.ctx.storage.get<string>(`state:${name}`);
      return v === undefined ? new Response("not found", { status: 404 }) : new Response(v);
    }
    if (request.method === "PUT") {
      const body = await request.text();
      if (body.length > 1_500_000) return new Response("too large", { status: 413 });
      await this.ctx.storage.put(`state:${name}`, body);
      return new Response("ok");
    }
    return new Response("method not allowed", { status: 405 });
  }

  private async startAndWait(): Promise<void> {
    const container = (this.ctx as any).container;
    if (!container.running) {
      const env: Record<string, string> = { PUBLIC_URL: this.env.PUBLIC_URL, DEMO_TODAY: this.env.DEMO_TODAY,
        STATE_REMOTE: "1" };
      for (const k of SECRET_KEYS) if (this.env[k]) env[k] = this.env[k];
      // The Enable Banking PEM key (3 KB, multi-line) doesn't reach the container as one variable: send its base64
      // body in 1000-character parts, EB_KEY_0, EB_KEY_1, ... (app/enable_banking.py puts it back together).
      if (this.env.EB_PRIVATE_KEY) {
        delete env.EB_PRIVATE_KEY;
        const body = String(this.env.EB_PRIVATE_KEY).replace(/-----[^-]+-----|\\n|\s/g, "");
        for (let i = 0; i * 1000 < body.length; i++) env[`EB_KEY_${i}`] = body.slice(i * 1000, (i + 1) * 1000);
      }
      container.start({ image: container.images.base, instance: "basic", enableInternet: true, env });
    }
    this.observeExit();
    await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);
    const port = container.getTcpPort(PORT);
    for (let attempt = 0; attempt < 150; attempt++) {
      try {
        const r = await port.fetch("http://container/health", { signal: AbortSignal.timeout(1000) });
        await r.body?.cancel();
        if (r.ok) return;
      } catch {
        // not listening yet
      }
      await scheduler.wait(200);
    }
    throw new Error("Container did not become ready");
  }

  private observeExit(): void {
    if (this.monitoring) return;
    this.monitoring = (this.ctx as any).container.monitor()
      .then(() => console.log("container exited"))
      .catch((e: unknown) => console.error("container failed:", e))
      .finally(() => { this.monitoring = undefined; });
    this.ctx.waitUntil(this.monitoring!);
  }
}

const STT_MODEL = "@cf/openai/whisper-large-v3-turbo";
const STT_MAX_JSON = 12_000_000; // ~8 MB of audio as base64 (app/voice.py MAX_BYTES)

// POST /__stt?key=<WA_VERIFY_TOKEN> {audio: base64, initial_prompt?, vad_filter?} -> {text}
// Voice notes for the app (app/voice.py): Whisper on Workers AI through the AI binding, so no API token in the container.
async function stt(request: Request, env: any, u: URL): Promise<Response> {
  if (!env.WA_VERIFY_TOKEN || u.searchParams.get("key") !== env.WA_VERIFY_TOKEN) return new Response("forbidden", { status: 403 });
  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
  const raw = await request.text();
  if (raw.length > STT_MAX_JSON) return new Response("too large", { status: 413 });
  const { audio, initial_prompt, vad_filter } = JSON.parse(raw);
  if (typeof audio !== "string" || !audio) return new Response("audio missing", { status: 400 });
  const out = await env.AI.run(STT_MODEL, { audio, initial_prompt, vad_filter: vad_filter === true });
  return Response.json({ text: out?.text ?? "" });
}

export default {
  async fetch(request: Request, env: any): Promise<Response> {
    const u = new URL(request.url);
    if (u.pathname === "/__stt") return stt(request, env, u);
    // State lives in one object that outlasts every container, whatever INSTANCE is running.
    if (u.pathname.startsWith("/__state/")) return env.COACH.getByName("state").fetch(request);
    // Bump INSTANCE (wrangler.jsonc) to start a fresh container with new secrets or a new image.
    return env.COACH.getByName(`main-${env.INSTANCE || "1"}`).fetch(request);
  },

  // Cron (wrangler.jsonc triggers, UTC): proactive messages. Off unless DEMO_PROACTIVE=1, so by default the cron
  // does not even wake the container; the app checks the same flag again before sending anything.
  async scheduled(_controller: unknown, env: any): Promise<void> {
    if (env.DEMO_PROACTIVE !== "1" || !env.WA_VERIFY_TOKEN) return;
    const url = `https://container/cron/wake?key=${encodeURIComponent(env.WA_VERIFY_TOKEN)}`;
    const r = await env.COACH.getByName(`main-${env.INSTANCE || "1"}`).fetch(new Request(url, { method: "POST" }));
    console.log("cron wake", r.status); // never the body: it quotes amounts
    await r.body?.cancel();
  },
};
