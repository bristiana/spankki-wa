// Cloudflare Worker in front of the Python app. One container instance ("main") holds the demo state,
// so WhatsApp, the confirm pages and the web chat all see the same balance.
import { DurableObject } from "cloudflare:workers";

const PORT = 8080;
const INACTIVITY_TIMEOUT_MS = 60 * 60 * 1000; // keep the demo warm for an hour after the last request

const SECRET_KEYS = ["ANTHROPIC_API_KEY", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_APP_SECRET", "WA_VERIFY_TOKEN",
  "DEMO_PHONE", "WA_DISPLAY_NUMBER"] as const;

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
    this.starting ??= this.startAndWait().finally(() => { this.starting = undefined; });
    await this.starting;
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return (this.ctx as any).container.getTcpPort(PORT).fetch(forwarded);
  }

  private async startAndWait(): Promise<void> {
    const container = (this.ctx as any).container;
    if (!container.running) {
      const env: Record<string, string> = { PUBLIC_URL: this.env.PUBLIC_URL, DEMO_TODAY: this.env.DEMO_TODAY };
      for (const k of SECRET_KEYS) if (this.env[k]) env[k] = this.env[k];
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

export default {
  async fetch(request: Request, env: any): Promise<Response> {
    return env.COACH.getByName("main").fetch(request);
  },
};
