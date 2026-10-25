import { NextResponse, type NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname !== "/pitch") return;
  return NextResponse.redirect(new URL("/pitch/", request.url), 308);
}

export const config = { matcher: ["/pitch"] };
