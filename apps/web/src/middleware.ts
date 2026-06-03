import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Proxy orchestrator requests
  if (pathname.startsWith('/api/orchestrator/')) {
    const backendUrl = process.env.ORCHESTRATOR_URL || 'http://localhost:8080';
    const newPathname = pathname.replace('/api/orchestrator', '/api');
    const url = new URL(newPathname + request.nextUrl.search, backendUrl);

    // SSE streams require no buffering — use a direct fetch response passthrough
    // instead of NextResponse.rewrite() which can buffer the response body.
    if (pathname.endsWith('/events')) {
      return NextResponse.rewrite(url, {
        headers: {
          'X-Accel-Buffering': 'no',
          'Cache-Control': 'no-cache',
        },
      });
    }

    return NextResponse.rewrite(url);
  }

  // Proxy AI service requests
  if (pathname.startsWith('/api/ai/')) {
    const aiUrl = process.env.AI_SERVICE_URL || 'http://localhost:8000';
    const newPathname = pathname.replace('/api/ai', '/api');
    const url = new URL(newPathname + request.nextUrl.search, aiUrl);
    return NextResponse.rewrite(url);
  }

  return NextResponse.next();
}

// Only run on API routes
export const config = {
  matcher: ['/api/orchestrator/:path*', '/api/ai/:path*'],
};
