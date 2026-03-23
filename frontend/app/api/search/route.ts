import { NextRequest, NextResponse } from "next/server";
import { invokeSearch } from "@/lib/tensorlake";

export async function POST(request: NextRequest) {
  const body = await request.json();
  const { query, website } = body;

  if (!query || !website) {
    return NextResponse.json(
      { error: "query and website are required" },
      { status: 400 }
    );
  }

  try {
    const result = await invokeSearch(query, website);
    return NextResponse.json(result);
  } catch (error) {
    console.error("Failed to invoke search:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
