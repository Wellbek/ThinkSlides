import { NextRequest, NextResponse } from 'next/server';

// Same server-side variable the middleware proxy uses, so both hops agree on
// where the orchestrator lives. It is deliberately not NEXT_PUBLIC_*: this
// runs on the server, and the browser must not need to know the address.
const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://localhost:8080';

/** The orchestrator serves everything under /api; the proxy prefix is ours. */
function upstream(slug: string[] | undefined, search: string): string {
  const path = slug ? slug.join('/') : '';
  return `${ORCHESTRATOR_URL}/api/${path}${search}`;
}

export async function GET(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const url = upstream(params.slug, request.nextUrl.search);
  
  console.log('Proxying GET:', url);

  try {
    // Forward necessary headers
    const headers: HeadersInit = {};
    const authHeader = request.headers.get('Authorization');
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const response = await fetch(url, {
      method: 'GET',
      headers,
    });

    const contentType = response.headers.get('Content-Type');

    // For SSE (text/event-stream), stream the response
    if (contentType === 'text/event-stream') {
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Response body is null');
      }

      const encoder = new TextEncoder();
      const decoder = new TextDecoder();

      // Create a ReadableStream that forwards chunks as they arrive
      const stream = new ReadableStream({
        async start(controller) {
          // Node holds the response head back until the body's first byte, so
          // an idle session would leave EventSource unopened until the
          // orchestrator's 20s heartbeat - defeating the immediate flush it
          // does precisely so the stream reports open while nothing is
          // happening. A comment line comes straight back out and is ignored
          // by EventSource.
          controller.enqueue(encoder.encode(': connected\n\n'));
          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              const chunk = decoder.decode(value, { stream: true });
              controller.enqueue(encoder.encode(chunk));
            }
            controller.close();
          } catch (err) {
            console.error('Stream error:', err);
            controller.error(err);
          }
        },
      });

      return new Response(stream, {
        status: response.status,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
          'X-Accel-Buffering': 'no', // Disable nginx/proxy buffering
        },
      });
    } else {
      // For non-SSE responses (JSON, etc.), read all and return
      const data = await response.text();
      return new NextResponse(data, {
        status: response.status,
        headers: {
          'Content-Type': contentType || 'application/json',
        },
      });
    }
  } catch (error) {
    console.error('Proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to reach orchestrator service' },
      { status: 502 }
    );
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const url = upstream(params.slug, request.nextUrl.search);
  
  console.log('Proxying POST:', url);

  try {
    const body = await request.text();
    const authHeader = request.headers.get('Authorization');

    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body,
    });

    const data = await response.text();
    return new NextResponse(data, {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('Content-Type') || 'application/json',
      },
    });
  } catch (error) {
    console.error('Proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to reach orchestrator service' },
      { status: 502 }
    );
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const url = upstream(params.slug, request.nextUrl.search);
  
  console.log('Proxying DELETE:', url);

  try {
    const authHeader = request.headers.get('Authorization');
    const headers: HeadersInit = {};
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const response = await fetch(url, {
      method: 'DELETE',
      headers,
    });

    const data = await response.text();
    return new NextResponse(data, {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('Content-Type') || 'application/json',
      },
    });
  } catch (error) {
    console.error('Proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to reach orchestrator service' },
      { status: 502 }
    );
  }
}