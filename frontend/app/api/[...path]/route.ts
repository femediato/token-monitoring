// Server-side proxy: the browser only talks to this app; the API stays on the internal network.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const search = new URL(req.url).search;
  try {
    const upstream = await fetch(`${API_URL}/api/${path.map(encodeURIComponent).join("/")}${search}`, {
      cache: "no-store",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ detail: "API unreachable" }, { status: 502 });
  }
}
