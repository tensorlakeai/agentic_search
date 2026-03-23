import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const TENSORLAKE_API_URL =
  process.env.TENSORLAKE_API_URL || "https://api.tensorlake.ai";
const TENSORLAKE_APP_NAME =
  process.env.TENSORLAKE_APP_NAME || "agentic_search";

function friendlyName(functionName: string): string {
  const names: Record<string, string> = {
    agentic_search: "Agentic search",
    search_site: "Site search",
    fetch_page: "Page fetch",
    read_document: "Document reader",
    document_to_markdown: "Document conversion",
    download_file: "File download",
  };
  return names[functionName] || functionName;
}

async function produceEvents(
  writer: WritableStreamDefaultWriter<Uint8Array>,
  encoder: TextEncoder,
  requestId: string
) {
  const send = async (event: string, data: unknown) => {
    await writer.write(
      encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    );
  };

  try {
    await send("connected", { requestId });

    const progressUrl = `${TENSORLAKE_API_URL}/applications/${TENSORLAKE_APP_NAME}/requests/${requestId}/progress`;
    console.log(`[stream] SSE connecting: ${progressUrl}`);

    const sseRes = await fetch(progressUrl, {
      headers: {
        Authorization: `Bearer ${process.env.TENSORLAKE_API_KEY}`,
      },
      cache: "no-store",
    });

    if (!sseRes.ok || !sseRes.body) {
      const text = await sseRes.text().catch(() => "");
      console.error(`[stream] SSE failed ${sseRes.status}: ${text}`);
      await send("error", { error: `Progress stream failed (${sseRes.status})` });
      return;
    }

    const reader = sseRes.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let gotResult = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const chunk = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        for (const line of chunk.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed || trimmed === ":") continue;

          if (trimmed.startsWith("data: ")) {
            const raw = trimmed.slice(6);
            console.log(`[stream] SSE event: ${raw.slice(0, 200)}`);

            try {
              const data = JSON.parse(raw);
              const eventType = Object.keys(data)[0];
              const payload = data[eventType];

              switch (eventType) {
                case "FunctionRunCreated": {
                  await send("progress", {
                    type: "started",
                    function_name: payload.function_name,
                    message: `${friendlyName(payload.function_name)} started...`,
                  });
                  break;
                }
                case "FunctionRunCompleted": {
                  const ok = payload.outcome === "success";
                  await send("progress", {
                    type: "completed",
                    function_name: payload.function_name,
                    outcome: payload.outcome,
                    message: `${friendlyName(payload.function_name)} ${ok ? "completed" : "failed"}`,
                  });
                  break;
                }
                case "RequestFinished": {
                  const output = payload.output?.body;
                  if (output) {
                    await send("result", output);
                  }
                  gotResult = true;
                  break;
                }
                case "AllocationCreated":
                case "AllocationCompleted":
                  break;
                default: {
                  const message =
                    payload?.message || payload?.msg || eventType;
                  await send("progress", { type: eventType, message });
                  break;
                }
              }
            } catch (e) {
              console.error(`[stream] Parse error: ${raw.slice(0, 100)}`, e);
            }
          }
        }

        boundary = buffer.indexOf("\n\n");
      }
    }

    // Fallback: fetch output directly if no RequestFinished event
    if (!gotResult) {
      console.log(`[stream] No RequestFinished, fetching output directly`);
      const outputUrl = `${TENSORLAKE_API_URL}/applications/${TENSORLAKE_APP_NAME}/requests/${requestId}/output`;
      const outputRes = await fetch(outputUrl, {
        headers: {
          Authorization: `Bearer ${process.env.TENSORLAKE_API_KEY}`,
          Accept: "application/json",
        },
      });
      if (outputRes.ok) {
        const output = await outputRes.json();
        await send("result", output);
      }
    }

    await send("done", {});
  } catch (error) {
    console.error(`[stream] Error:`, error);
    try {
      await writer.write(
        encoder.encode(
          `event: error\ndata: ${JSON.stringify({ error: error instanceof Error ? error.message : "Stream error" })}\n\n`
        )
      );
    } catch {
      // writer may already be closed
    }
  } finally {
    try {
      await writer.close();
    } catch {
      // already closed
    }
  }
}

export async function GET(request: NextRequest) {
  const requestId = request.nextUrl.searchParams.get("requestId");

  if (!requestId) {
    return new Response("requestId query parameter is required", {
      status: 400,
    });
  }

  const encoder = new TextEncoder();
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();

  // Start producing events in the background — do NOT await
  produceEvents(writer, encoder, requestId);

  return new Response(readable, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
